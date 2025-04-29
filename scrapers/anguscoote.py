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
import httpx
from utils import get_public_ip, log_event, sanitize_filename
from dotenv import load_dotenv
from database import insert_into_db
from limit_checker import update_product_count
from io import BytesIO

# Load environment variables from .env file
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')


def modify_image_url(image_url):
    """Modify the image URL to replace '_260' with '_1200' while keeping query parameters."""
    if not image_url or image_url == "N/A":
        return image_url

    # Extract and preserve query parameters
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"

    # Replace '_260' with '_1200' while keeping the rest of the URL intact
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)

    return modified_url + query_params  # Append query parameters if they exist

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

async def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def scroll_and_wait(page):
    """Scroll down to load lazy-loaded products."""
    previous_height = await page.evaluate("document.body.scrollHeight")
    await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
    await asyncio.sleep(2)  # Allow time for content to load
    new_height = await page.evaluate("document.body.scrollHeight")
    return new_height > previous_height  # Returns True if more content is loaded

async def safe_goto_and_wait(page, url, retries=3):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")
            
            # Wait for either product cards or "no products" message
            try:
                product_cards = await page.wait_for_selector(".ProductCardWrapper, .ps-category-items", timeout=15000)
                if product_cards:
                    print("[Success] Product container loaded.")
                    return True
            except TimeoutError:
                if attempt == retries - 1:
                    print("[Warning] No products found on page")
                    return False
                
            # Check for empty results
            empty_indicator = await page.query_selector("div.message.empty")
            if empty_indicator and "no products" in (await empty_indicator.inner_text()).lower():
                print("[Info] Reached end of product list")
                return False

        except Exception as e:
            print(f"[Retry {attempt + 1}] Error: {e}")
            await asyncio.sleep(2)

    raise Exception(f"Failed to load {url} after {retries} attempts")

async def handle_anguscoote(url, max_pages):
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
    filename = f"anguscoote_data_{timestamp}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        base_url = url.split('?')[0]
        current_url = f"{base_url}?p={page_count}" if page_count > 1 else base_url
        
        logging.info(f"Processing page {page_count}: {current_url}")
        
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

                # Scroll to load content
                scroll_attempts = 0
                while scroll_attempts < 3 and await scroll_and_wait(page):
                    scroll_attempts += 1
                    await random_delay(1, 2)

                # Process products on current page
                product_wrapper = await page.query_selector("div.ps-category-items")
                products = await product_wrapper.query_selector_all("div.ps-category-item") if product_wrapper else []
                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        # Extract product data
                        name_elem = await product.query_selector("div.s-product__name")
                        price_elem = await product.query_selector("span.s-price__now")
                        img_elem = await product.query_selector("img")

                        product_name = await name_elem.inner_text() if name_elem else "N/A"
                        price = await price_elem.inner_text() if price_elem else "N/A"
                        image_url = await img_elem.get_attribute("src") if img_elem else "N/A"
                        if not image_url and img_elem:
                            image_url = await img_elem.get_attribute("data-src") or "N/A"

                        # Extract gold and diamond info
                        kt = re.search(r"\b\d+K\s+\w+\s+\w+\b", product_name).group() if re.search(r"\b\d+K\s+\w+\s+\w+\b", product_name) else "N/A"
                        diamond = re.search(r"\d+[-/]?\d*/?\d*\s*ct\s*tw", product_name).group() if re.search(r"\d+[-/]?\d*/?\d*\s*ct\s*tw", product_name) else "N/A"

                        unique_id = str(uuid.uuid4())
                        image_tasks.append((row_num, unique_id, asyncio.create_task(
                            download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                        )))

                        records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond))
                        sheet.append([current_date, page_title, product_name, None, kt, price, diamond, time_only, image_url])

                    except Exception as e:
                        logging.error(f"Error extracting product data: {e}")
                        continue

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

    # Prepare data for database insertion
    db_data = []
    current_date = datetime.now().strftime("%Y-%m-%d")
    for record in all_records:
        db_entry = (
            record[0],  # unique_id
            current_date,
            record[2],  # page_title
            record[3],  # product_name
            record[4],  # image_path
            record[5],  # kt
            record[6],  # price
            record[7]   # diamond
        )
        db_data.append(db_entry)

    insert_into_db(db_data)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path