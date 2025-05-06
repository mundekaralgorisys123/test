import asyncio
import re
import os
import uuid
import logging
import base64
import random
import time
from datetime import datetime
from io import BytesIO

import httpx
from PIL import Image as PILImage
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from flask import Flask
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError, Error

from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
from proxysetup import get_browser_with_proxy_strategy
# Load environment
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Flask and paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

# Resize image if needed
def resize_image(image_data, max_size=(100, 100)):
    try:
        img = PILImage.open(BytesIO(image_data))
        img.thumbnail(max_size, PILImage.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception as e:
        log_event(f"Error resizing image: {e}")
        return image_data

# Transform URL to get high-res image
def modify_image_url(image_url):
    if not image_url or image_url == "N/A":
        return image_url
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)
    return modified_url + query_params

# Async image downloader
async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
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

# Human-like delay
def random_delay(min_sec=1, max_sec=3):
    time.sleep(random.uniform(min_sec, max_sec))



def build_url_with_loadmore(base_url: str, page_count: int) -> str:
    separator = '&' if '?' in base_url else '?'
    return f"{base_url}{separator}p={page_count}"   

# Main scraper function
async def handle_gabriel(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} from IP: {ip_address}, max_pages: {max_pages}")

    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    all_records = []
    filename = f"handle_gabriel_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1


    while page_count <= max_pages:
        current_url= build_url_with_loadmore(url, page_count)
        logging.info(f"Processing page {page_count}: {current_url}")
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                
                product_wrapper=".ProductCardWrapper"
                browser, page = await get_browser_with_proxy_strategy(p, current_url,product_wrapper)
                

                # Scroll to load all items
                prev_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))
                    count = await page.locator('.ProductCardWrapper').count()
                    if count == prev_count:
                        break
                    prev_count = count

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                wrapper = page.locator("div.qd-product-list").first
                products = await wrapper.locator("div.ProductCard").all() if await wrapper.count() > 0 else []

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        product_name = await product.locator("a.ProductCTA").inner_text()
                    except:
                        product_name = "N/A"


                    try:
                        price_wrapper = product.locator('.price-wrapper')
                        price = await price_wrapper.locator('.price').text_content() if await price_wrapper.count() > 0 else "N/A"
                    except:
                        price = "N/A"

                    try:
                        product_images = await product.locator("img.image-entity").all()
                        product_urls = [await img.get_attribute("data-src") for img in product_images if (await img.get_attribute("data-src")).startswith("https://")]
                        image_url = product_urls[0] if product_urls else "N/A"
                    except:
                        image_url = "N/A"

                    try:
                        metal_el = product.locator("span.metalIcon")
                        kt = await metal_el.get_attribute("data-originalmetal") if await metal_el.count() > 0 else "N/A"
                    except:
                        kt = "N/A"

                    dia_match = re.search(r"\b(\d+(\.\d+)?)\s*(?:ct|ctw|carat)\b", product_name, re.IGNORECASE)
                    diamond_weight = f"{dia_match.group(1)} ct" if dia_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight))
                    sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])

                for row_num, unique_id, task in image_tasks:
                    try:
                        image_path = await asyncio.wait_for(task, timeout=60)
                        if image_path != "N/A":
                            try:
                                img = ExcelImage(image_path)
                                img.width, img.height = 100, 100
                                sheet.add_image(img, f"D{row_num}")
                            except Exception as e:
                                logging.error(f"Error embedding image: {e}")
                                image_path = "N/A"
                        for i, record in enumerate(records):
                            if record[0] == unique_id:
                                records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7])
                                break
                    except asyncio.TimeoutError:
                        logging.warning(f"Image download timed out for row {row_num}")

                all_records.extend(records)                
                wb.save(file_path)

        except Exception as e:
            logging.error(f"Error on page {page_count}: {str(e)}")
            wb.save(file_path)
        finally:
            if page: await page.close()
            if browser: await browser.close()
            await asyncio.sleep(random.uniform(2, 5))

        page_count += 1

    wb.save(file_path)
    log_event(f"Data saved to {file_path}")
    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path
