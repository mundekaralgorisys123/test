import os
import time
import logging
import aiohttp
import asyncio
import concurrent.futures
from datetime import datetime
from io import BytesIO
from playwright.async_api import async_playwright, Page, TimeoutError, Error
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from flask import Flask
import uuid
import base64
from dotenv import load_dotenv
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db, create_table
from limit_checker import update_product_count
import random
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
# Load environment variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Setup Flask
app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def modify_image_url(image_url):
    """Update Helzberg image URL to use high resolution (800x800)."""
    if not image_url or image_url == "N/A":
        return image_url

    # Parse the URL
    parsed_url = urlparse(image_url)
    query = parse_qs(parsed_url.query)

    # Modify or add resolution parameters
    query["sw"] = ["800"]
    query["sh"] = ["800"]
    query["sm"] = ["fit"]

    # Rebuild the URL with updated query
    new_query = urlencode(query, doseq=True)
    high_res_url = urlunparse(parsed_url._replace(query=new_query))

    return high_res_url

async def download_image(session, image_url, product_name, timestamp, image_folder, retries=3):
    """Download image with retries and return its local path."""
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{sanitize_filename(product_name)}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    
    modified_url = modify_image_url(image_url)

    for attempt in range(retries):
        try:
            async with session.get(modified_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(await response.read())
                return image_full_path
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")
            await asyncio.sleep(1)  # Add small delay between retries

    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"

async def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def scroll_and_wait(page: Page):
    """Scroll down to load lazy-loaded products."""
    previous_height = await page.evaluate("document.body.scrollHeight")
    await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
    await asyncio.sleep(2)  # Allow time for content to load
    new_height = await page.evaluate("document.body.scrollHeight")
    return new_height > previous_height  # Returns True if more content is loaded

async def handle_rosssimons(url, max_pages):
    """Scrape product data from Ross Simons website using fresh browser instances for each page."""
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} | IP: {ip_address} | Max pages: {max_pages}")

    # Prepare folders
    if not os.path.exists(EXCEL_DATA_PATH):
        os.makedirs(EXCEL_DATA_PATH)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Prepare Excel
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")

    # Collect all data across pages
    all_records = []
    row_counter = 2  # Start from row 2 (header is row 1)

    current_url = url
    pages_processed = 0

    while current_url and pages_processed < max_pages:
        try:
            # Create fresh browser instance for each page
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                page = await browser.new_page()
                
                logging.info(f"Processing page {pages_processed + 1}: {current_url}")
                await page.goto(current_url, timeout=180000, wait_until="domcontentloaded")
                pages_processed += 1

                # Scroll to load lazy-loaded content
                scroll_attempts = 0
                max_scroll_attempts = 5
                while scroll_attempts < max_scroll_attempts and await scroll_and_wait(page):
                    scroll_attempts += 1
                    await random_delay(1, 3)

                # Get page title
                page_title = await page.title()

                # Extract products
                product_wrapper = await page.query_selector("div.row.product-grid")
                products = await product_wrapper.query_selector_all("div.col-6.col-sm-3") if product_wrapper else []
                if pages_processed > 1:
                    products = await page.query_selector_all("div.col-6.col-sm-3")
                logging.info(f"Found {len(products)} products on page {pages_processed}")

                # Process products on this page
                async with aiohttp.ClientSession() as session:
                    for product in products:
                        # Extract product name
                        product_name_tag = await product.query_selector('.product-tile-name .pdp-link a')
                        product_name = (await product_name_tag.text_content()).strip() if product_name_tag else "N/A"

                        # Extract price
                        price_tag = await product.query_selector('.sales .z-price')
                        price = (await price_tag.text_content()).strip() if price_tag else "N/A"

                        # Extract image URL
                        image_tag = await product.query_selector('picture img')
                        image_url = await image_tag.get_attribute('src') if image_tag else "N/A"

                        # Extract metal type
                        gold_type_match = re.findall(r"(\d{1,2}K[t]?\s*(?:Yellow|White|Rose)?\s*Gold)", product_name, re.IGNORECASE)
                        kt = ", ".join(gold_type_match) if gold_type_match else "N/A"

                        # Extract Diamond Weight
                        diamond_weight_match = re.findall(r"(\d+(?:[-/]\d+)?(?:\.\d+)?\s*ct\.?\s*t\.?w\.?)", product_name, re.IGNORECASE)
                        diamond_weight = ", ".join(diamond_weight_match) if diamond_weight_match else "N/A"
                        
                        # Download image immediately while browser is still open
                        image_path = await download_image(session, image_url, product_name, timestamp, image_folder)
                        
                        unique_id = str(uuid.uuid4())
                        all_records.append((unique_id, current_date, page_title, product_name, image_path, kt, price, diamond_weight))
                        
                        # Add to Excel
                        sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])
                        
                        # Add image to Excel if downloaded successfully
                        if image_path != "N/A":
                            try:
                                img = Image(image_path)
                                img.width, img.height = 100, 100
                                sheet.add_image(img, f"D{row_counter}")
                            except Exception as e:
                                logging.error(f"Error adding image to Excel: {e}")
                        
                        row_counter += 1

                # Find next page URL from "More Results" button
                show_more_div = await page.query_selector('div.show-more')
                if show_more_div:
                    more_button = await show_more_div.query_selector('button.more')
                    if more_button:
                        current_url = await more_button.get_attribute('data-url')
                        logging.info(f"Found next page URL: {current_url}")
                    else:
                        current_url = None
                else:
                    current_url = None

                await browser.close()
                await random_delay(2, 4)  # Be polite between pages

        except Exception as e:
            logging.error(f"Error processing page {pages_processed}: {e}")
            if 'browser' in locals():
                await browser.close()
            break

    # Save Excel file
    filename = f"rosssimons_{current_date}_{time_only}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)
    wb.save(file_path)
    logging.info(f"Data saved to {file_path}")

    # Encode file in base64
    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    # Batch insert data into DB
    insert_into_db(all_records)

    # Update product count
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path
