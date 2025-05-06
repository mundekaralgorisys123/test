import os
import re
import time
import logging
import random
import uuid
import asyncio
import base64
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError, Error
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from flask import Flask
from PIL import Image as PILImage
import requests
import concurrent.futures
from utils import get_public_ip, log_event, sanitize_filename
from dotenv import load_dotenv
from database import insert_into_db
from limit_checker import update_product_count
import aiohttp
from io import BytesIO
from openpyxl.drawing.image import Image as XLImage
import httpx
# Load environment variables from .env file
from functools import partial
from proxysetup import get_browser_with_proxy_strategy
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

async def download_and_resize_image(session, image_url):
    try:
        async with session.get(modify_image_url(image_url), timeout=10) as response:
            if response.status != 200:
                return None
            content = await response.read()
            image = PILImage.open(BytesIO(content))
            image.thumbnail((200, 200))
            img_byte_arr = BytesIO()
            image.save(img_byte_arr, format='JPEG', optimize=True, quality=85)
            return img_byte_arr.getvalue()
    except Exception as e:
        logging.warning(f"Error downloading/resizing image: {e}")
        return None

def modify_image_url(image_url: str) -> str:
    """Modify the image URL to request high resolution by changing the URL parameters."""
    if not image_url or image_url == "N/A":
        return image_url

    # Check if the URL already contains a width parameter and change it to the high-res version
    if "600x600" in image_url:
        image_url = image_url.replace("600x600", "1220x1220")
    elif "600x600" not in image_url and "x600" in image_url:
        image_url = image_url.replace("x600", "x1220")
    
    return image_url

async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)

    high_res_url = modify_image_url(image_url)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Try to download the high-resolution image first
        for attempt in range(retries):
            try:
                response = await client.get(high_res_url)
                response.raise_for_status()  # Check if the response is successful (200)
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path
            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - High-res failed for {product_name}: {e}")

        # Fallback to the low-resolution image if high-res download fails
        try:
            response = await client.get(image_url)
            response.raise_for_status()
            with open(image_full_path, "wb") as f:
                f.write(response.content)
            return image_full_path
        except httpx.RequestError as e:
            logging.error(f"Fallback failed for {product_name}: {e}")
            return "N/A"

async def handle_shiels(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} from IP: {ip_address}, max_pages: {max_pages}")

    # Prepare directories and files
    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Create workbook and setup
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    all_records = []
    filename = f"handle_shiels_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        if page_count > 1:
            if '#' in url:
                base, fragment = url.split('#', 1)
                current_url = f"{base}?page={page_count}#{fragment}"
            else:
                current_url = f"{url}?page={page_count}"
        logging.info(f"Processing page {page_count}: {current_url}")
        
        # Create a new browser instance for each page
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                product_wrapper = ".ProductGridContainer"
                browser, page = await get_browser_with_proxy_strategy(p,current_url, product_wrapper)
                log_event(f"Successfully loaded: {current_url}")
                # Scroll to load all products
                prev_product_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))  # Random delay between scrolls
                    current_product_count = await page.locator('.ProductGridContainer').count()
                    if current_product_count == prev_product_count:
                        break
                    prev_product_count = current_product_count


                product_wrapper = await page.query_selector("div.ProductGridContainer")
                products = await product_wrapper.query_selector_all("li.column.ss__result.ss__result--item")
                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        product_name_tag = await product.query_selector("a.product-card-title")
                        product_name = await product_name_tag.inner_text() if product_name_tag else "N/A"
                    except:
                        product_name = "N/A"

                    try:
                        price_tag = await product.query_selector("span.price")
                        if price_tag:
                            price_was_tag = await price_tag.query_selector("span.ss__result__msrp")
                            price_now_tag = await price_tag.query_selector("span.discounted")
                            
                            price_was = await price_was_tag.inner_text() if price_was_tag else None
                            price_now = await price_now_tag.inner_text() if price_now_tag else None

                            if not price_now:
                                price_now_tag_alt = await price_tag.query_selector("span.font-bold")
                                price_now = await price_now_tag_alt.inner_text() if price_now_tag_alt else "N/A"

                            price_was = price_was or price_now
                        else:
                            price_was, price_now = "N/A", "N/A"
                    except:
                        price_was, price_now = "N/A", "N/A"

                    try:
                        image_tag = await product.query_selector("img.product-primary-image")
                        image_url = await image_tag.get_attribute("src") if image_tag else "N/A"
                    except:
                        image_url = "N/A"



                    gold_type_match = re.search(r"\b(\d{1,2}[Kk])\s*(\w+\s*\w*)\b", product_name)
                    kt = gold_type_match.group() if gold_type_match else "Not found"

                    diamond_weight_match = re.search(r"(\d+[-/]?\d*\s*(?:ct|carat)\s*(?:tw)?)", product_name)
                    diamond_weight = diamond_weight_match.group() if diamond_weight_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price_now, diamond_weight))
                    sheet.append([current_date, page_title, product_name, None, kt, price_now, diamond_weight, time_only, image_url])

                # Process images and update records
                for row_num, unique_id, task in image_tasks:
                    try:
                        image_path = await asyncio.wait_for(task, timeout=60)
                        if image_path != "N/A":
                            try:
                                img = Image(image_path)
                                img.width, img.height = 100, 100
                                sheet.add_image(img, f"D{row_num}")
                            except Exception as img_error:
                                logging.error(f"Error adding image to Excel: {img_error}")
                                image_path = "N/A"
                        
                        for i, record in enumerate(records):
                            if record[0] == unique_id:
                                records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7])
                                break
                    except asyncio.TimeoutError:
                        logging.warning(f"Timeout downloading image for row {row_num}")

                all_records.extend(records)
                success_count += 1

                # Save progress after each page
                wb.save(file_path)
                logging.info(f"Progress saved after page {page_count}")

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
            
        page_count += 1


    # Final save and database operations
    wb.save(file_path)
    log_event(f"Data saved to {file_path}")

    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path
