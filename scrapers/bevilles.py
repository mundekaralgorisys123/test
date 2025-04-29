import os
import re
import uuid
import logging
import random
import asyncio
import base64
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError, Error
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from flask import Flask
from dotenv import load_dotenv
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db, create_table
from limit_checker import update_product_count
import httpx
from io import BytesIO

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')


def modify_image_url(image_url):
    """Modify the image URL to replace any _### (e.g., _180, _260, _400, etc.) with _1200 while keeping query parameters."""
    if not image_url or image_url == "N/A":
        return image_url

    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"

    modified_url = re.sub(r'_(\d{2,4})(?=x?\.\w+$)', '_1200', image_url)
    return modified_url + query_params

async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    """Download image with retries and return its local path."""
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(retries):
            try:
                response = await client.get(modified_url)
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path
            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")

    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"

async def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def scroll_and_wait(page, max_attempts=10, wait_time=1):
    """Scroll down and wait for new content to load dynamically."""
    last_height = await page.evaluate("document.body.scrollHeight")

    for attempt in range(max_attempts):
        logging.info(f"Scroll attempt {attempt + 1}/{max_attempts}")
        
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")

        try:
            await page.wait_for_selector(".product-item", state="attached", timeout=3000)
        except:
            logging.info("No new content detected.")
        
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            logging.info("No more new content. Stopping scroll.")
            break
        
        last_height = new_height
        await asyncio.sleep(wait_time)

    logging.info("Finished scrolling.")
    return True

async def safe_goto_and_wait(page, url, retries=3):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")
            product_cards = await page.wait_for_selector(".ss__has-results", timeout=15000)
            if product_cards:
                print("[Success] Product cards loaded.")
                return True
        except Exception as e:
            print(f"[Retry {attempt + 1}] Error: {e}")
            await asyncio.sleep(2)
    raise Exception(f"[Error] Failed to load product cards on {url} after {retries} attempts.")

async def handle_bevilles(url, max_pages):
    """Async version of Bevilles scraper"""
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} | IP: {ip_address} | Max pages: {max_pages}")

    # Prepare folders
    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Prepare Excel workbook
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")
    page_count = 1

    all_records = []
    filename = f"handle_bevilles_{current_date}_{time_only}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    current_url = url
    prev_prod_count = 0
    while current_url and page_count <= max_pages:
        logging.info(f"Processing page {page_count}: {current_url}")
        # Create a new browser instance for each page
        browser = None
        page = None
        
        if page_count>1 :
            current_url = url + "?page=" + str(page_count)

        logging.info(f"Navigating to {current_url}")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(120000)  # 2 minute timeout

                if not await safe_goto_and_wait(page, current_url):
                    break

                await scroll_and_wait(page, max_attempts=8)

                page_title = await page.title()
                products = await page.query_selector_all(".ss__result")
                logging.info(f"Total products scraped on page {page_count}: {len(products)}")
                products = products[prev_prod_count:]
                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        # Extract product details
                        product_name_tag = await product.query_selector("a.boost-pfs-filter-product-item-title")
                        product_name = (await product_name_tag.inner_text()).strip() if product_name_tag else "N/A"

                        price_tag = await product.query_selector("span.boost-pfs-filter-product-item-sale-price")
                        price = (await price_tag.inner_text()).strip() if price_tag else "N/A"

                        image_tag = await product.query_selector("img.boost-pfs-filter-product-item-main-image")
                        if image_tag:
                            data_srcset = await image_tag.get_attribute("data-srcset") or ""
                            product_urls = [url.split(" ")[0] for url in data_srcset.split(",") if url.startswith("https://")]
                            image_url = product_urls[0] if product_urls else "N/A"
                        else:
                            image_url = "N/A"

                        # Extract Kt
                        gold_type_pattern = r"\b\d{1,2}K\s+\w+(?:\s+\w+)?\b"
                        gold_type_match = re.search(gold_type_pattern, product_name, re.IGNORECASE)
                        kt = gold_type_match.group() if gold_type_match else "Not found"

                        # Extract diamond weight
                        diamond_weight_pattern = r"(\d+(?:[./-]\d+)?(?:\s*/\s*\d+)?\s*ct(?:\s*tw)?)"
                        diamond_weight_match = re.search(diamond_weight_pattern, product_name, re.IGNORECASE)
                        diamond_weight = diamond_weight_match.group() if diamond_weight_match else "N/A"

                        # Schedule image download
                        unique_id = str(uuid.uuid4())
                        image_tasks.append((
                            row_num,
                            unique_id,
                            asyncio.create_task(
                                download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                        )))

                        records.append((
                            unique_id,
                            current_date,
                            page_title,
                            product_name,
                            None,  # Placeholder for image path
                            kt,
                            price,
                            diamond_weight
                        ))

                        sheet.append([
                            current_date,
                            page_title,
                            product_name,
                            None,  # Placeholder for image
                            kt,
                            price,
                            diamond_weight,
                            time_only,
                            image_url
                        ])

                    except Exception as e:
                        logging.error(f"Error processing product {row_num}: {e}")
                        continue

                # Process downloaded images
                for row_num, unique_id, task in image_tasks:
                    try:
                        image_path = await asyncio.wait_for(task, timeout=60)
                        if image_path != "N/A":
                            try:
                                img = ExcelImage(image_path)
                                img.width, img.height = 100, 100
                                sheet.add_image(img, f"D{row_num}")
                            except Exception as img_error:
                                logging.error(f"Error adding image to Excel: {img_error}")
                                image_path = "N/A"
                        
                        # Update record with actual image_path
                        for i, record in enumerate(records):
                            if record[0] == unique_id:
                                records[i] = (
                                    record[0],
                                    record[1],
                                    record[2],
                                    record[3],
                                    image_path,
                                    record[5],
                                    record[6],
                                    record[7]
                                )
                                break

                    except asyncio.TimeoutError:
                        logging.warning(f"Timeout downloading image for row {row_num}")

                all_records.extend(records)

                # Save progress after each page
                wb.save(file_path)
                logging.info(f"Progress saved after page {page_count}")
                page_count+=1
                prev_prod_count += len(products)
        except Exception as e:
            logging.error(f"Error processing page {page_count}: {str(e)}")
            # Save what we have so far
            wb.save(file_path)
        finally:
            # Clean up resources for this page
            if page:
                await page.close()
            if browser:
                await browser.close()
            
            # Add delay between pages
            await asyncio.sleep(random.uniform(2, 5))

    # Final save and database operations
    wb.save(file_path)
    logging.info(f"Data saved to {file_path}")

    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path