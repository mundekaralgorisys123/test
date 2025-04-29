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
from flask import Flask, jsonify
from dotenv import load_dotenv
from PIL import Image as PILImage
from io import BytesIO
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db, create_table
from limit_checker import update_product_count
from urllib.parse import urljoin
import httpx

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')


# Ensure directories exist
os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
os.makedirs(IMAGE_SAVE_PATH, exist_ok=True)

def modify_image_url(image_url):
    """Modify the image URL to replace '_260' with '_1200' while keeping query parameters."""
    if not image_url or image_url == "N/A":
        return image_url

    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"

    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)
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

async def safe_goto_and_wait(page, url, retries=3):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector("#product-cards", timeout=15000)
                print("[Success] Found #product-cards")
            except:
                print("[Fallback] Waiting for product cards using card selector...")
                await page.wait_for_selector("[data-testid='card']", timeout=15000)
            return True
        except Exception as e:
            print(f"[Retry {attempt + 1}] Error loading {url}: {e}")
            await asyncio.sleep(3)
    raise Exception(f"[Error] Failed to load product cards on {url} after {retries} attempts.")

async def scroll_page(page):
    """Scroll down to load lazy-loaded products."""
    prev_product_count = 0
    for _ in range(50):  # Adjust scroll attempts as needed
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)  # Wait for lazy-loaded content to render

        # Wait for at least one product card to appear (if not already)
        await page.wait_for_selector('[data-testid="card"]', timeout=15000)

        # Count current number of product cards
        current_product_count = await page.locator('[data-testid="card"]').count()

        if current_product_count == prev_product_count:
            break  # Stop if no new products were loaded
        prev_product_count = current_product_count

async def handle_bash(start_url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {start_url} from IP: {ip_address}, max_pages: {max_pages}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Initialize Excel
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Gold Type", "Price", "Total Dia Wt", "Time", "ImagePath"]
    sheet.append(headers)
    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H-%M-%S")

    all_records = []
    filename = f"handle_bash_{current_date}_{time_only}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 0
    current_url = start_url

    while current_url and (page_count < max_pages):
        logging.info(f"Processing page {page_count + 1}: {current_url}")
        
        # Create a new browser instance for each page
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(120000)  # 2 minute timeout

                if not await safe_goto_and_wait(page, current_url):
                    break

                await scroll_page(page)

                page_title = await page.title()
                product_container = await page.query_selector("#product-cards")
                products = await product_container.query_selector_all("[data-testid='card']") if product_container else []
                print(len(products))

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        product_name_tag = await product.query_selector("h3.cursor-pointer.text-base.font-bold.leading-4.text-onyx-Black.line-clamp-2.h-8.z-5")
                        product_name = (await product_name_tag.inner_text()).strip() if product_name_tag else "N/A"

                        price_tag = await product.query_selector("div[data-testid='price']")
                        price = (await price_tag.inner_text()).strip() if price_tag else "N/A"

                        image_tag = await product.query_selector("img[data-testid='image']")
                        image_url = await image_tag.get_attribute("src") if image_tag else "N/A"

                        gold_type_match = re.search(r"(\d{1,2}K|Platinum|Silver|Gold|White Gold|Yellow Gold|Rose Gold)", product_name, re.IGNORECASE)
                        kt = gold_type_match.group(0) if gold_type_match else "N/A"

                        diamond_weight_match = re.search(r"(\d+(\.\d+)?)\s*(ct|carat)", product_name, re.IGNORECASE)
                        diamond_weight = f"{diamond_weight_match.group(1)} ct" if diamond_weight_match else "N/A"

                        unique_id = str(uuid.uuid4())
                        image_tasks.append((
                            row_num,
                            unique_id,
                            asyncio.create_task(
                                download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                            )
                        ))

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
                logging.info(f"Progress saved after page {page_count + 1}")

                # Pagination Handling
                next_button = await page.query_selector("a[data-testid='next-page-icon']")
                next_link = urljoin(current_url, await next_button.get_attribute("href")) if next_button else None
                current_url = next_link
                page_count += 1

        except Exception as e:
            logging.error(f"Error processing page {page_count + 1}: {str(e)}")
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
    log_event(f"Data saved to {file_path}")

    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    update_product_count(len(all_records))
    insert_into_db(all_records)

    return base64_encoded, filename, file_path