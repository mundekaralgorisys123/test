import os
import time
import logging
import aiohttp
import asyncio
import concurrent.futures
from datetime import datetime
from io import BytesIO
from playwright.async_api import async_playwright, TimeoutError
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
from playwright.async_api import Page
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Load environment variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Setup Flask
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def scroll_and_wait(page):
    """Scroll down to load lazy-loaded products."""
    previous_height = await page.evaluate("document.body.scrollHeight")
    await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
    await asyncio.sleep(2)  # Allow time for content to load
    new_height = await page.evaluate("document.body.scrollHeight")
    return new_height > previous_height  # Returns True if more content is loaded

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

async def download_image(session, image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    """Download image with retries using aiohttp."""
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url)

    for attempt in range(retries):
        try:
            async with session.get(modified_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                content = await response.read()
                with open(image_full_path, "wb") as f:
                    f.write(content)
                return image_full_path
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")
            await asyncio.sleep(1)  # Add small delay between retries

    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"

async def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def click_load_more(page, max_pages=5, delay_range=(3, 5)):
    """Click 'Load More' button dynamically until no more pages are available."""
    load_more_clicks = 0

    while load_more_clicks < (max_pages - 1):
        try:
            load_more_button = await page.query_selector(".show-more-btn")

            if load_more_button and await load_more_button.is_visible():
                await page.evaluate("(btn) => btn.click()", load_more_button)
                load_more_clicks += 1
                logging.info(f"✅ Clicked 'Load More' button {load_more_clicks} times.")
                
                # Wait for new content to load
                await random_delay(*delay_range)

                # Ensure new content loaded by checking page height change
                previous_height = await page.evaluate("document.body.scrollHeight")
                await asyncio.sleep(2)  # Short wait before rechecking height
                new_height = await page.evaluate("document.body.scrollHeight")

                if previous_height == new_height:
                    logging.info("⚠️ No new content loaded after clicking 'Load More'. Stopping.")
                    break
            else:
                logging.info("🔹 'Load More' button not found or not visible. Stopping.")
                break
        except Exception as e:
            logging.warning(f"⚠️ Error clicking 'Load More': {e}")
            break

async def handle_helzberg(url, max_pages):
    """Scrape product data from Helzberg website using direct pagination URLs."""
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} | IP: {ip_address} | Max pages: {max_pages}")

    # Prepare folders
    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Prepare Excel
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")

    # Collect all data across pages
    all_records = []
    row_counter = 2
    current_url = url
    pages_processed = 0

    while current_url and pages_processed < max_pages:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                page = await browser.new_page()
                
                logging.info(f"Processing page {pages_processed + 1}: {current_url}")
                await page.goto(current_url, timeout=180000, wait_until="domcontentloaded")
                pages_processed += 1

                # Scroll to load lazy content
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)

                # Get page title
                page_title = await page.title()

                product_wrapper = await page.query_selector("div.row.product-grid")
                products = await product_wrapper.query_selector_all("div.col-6.col-sm-4") if product_wrapper else []
                if pages_processed > 1:
                    products = await page.query_selector_all("div.col-6.col-sm-4")
                logging.info(f"Found {len(products)} products on page {pages_processed}")

                # Process products on this page
                async with aiohttp.ClientSession() as session:
                    for product in products:
                        try:
                            # Product name
                            name_element = await product.query_selector("a.prodname-container__link")
                            product_name = await name_element.inner_text() if name_element else "N/A"
                            product_name = product_name.strip()

                            # Price
                            price_tag = await product.query_selector("span.value")
                            price = await price_tag.inner_text() if price_tag else "N/A"
                            price = price.strip()

                            # Image URL
                            images = await product.query_selector_all("img")
                            product_urls = []
                            for img in images:
                                src = await img.get_attribute("src")
                                if src:
                                    product_urls.append(src)
                            image_url = product_urls[0] if product_urls else "N/A"

                            # Metal type
                            gold_type_match = re.search(r"\b\d+K\s+\w+\s+\w+\b", product_name)
                            kt = gold_type_match.group() if gold_type_match else "Not found"

                            # Diamond Weight
                            diamond_weight_match = re.search(r"(\d+(?:\.\d+)?(?:[-/]\d+(?:\.\d+)?)?\s*ct\.?\s*t\.?w\.?)", product_name)
                            diamond_weight = diamond_weight_match.group() if diamond_weight_match else "N/A"

                            unique_id = str(uuid.uuid4())
                            
                            # Download image immediately while browser is still open
                            image_path = await download_image(session, image_url, product_name, timestamp, image_folder, unique_id)
                            
                            # Add record
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

                        except Exception as e:
                            logging.error(f"Error processing product: {e}")
                            continue

                # Find next page URL from "Load More" button
                show_more_div = await page.query_selector('div.show-more')
                if show_more_div:
                    more_button = await show_more_div.query_selector('button.more.show-more-btn')
                    if more_button:
                        current_url = await more_button.get_attribute('data-url')
                        logging.info(f"Found next page URL: {current_url}")
                    else:
                        current_url = None
                else:
                    current_url = None

                await browser.close()
                await random_delay(3, 5)  # Increased delay between pages

        except Exception as e:
            logging.error(f"Error processing page {pages_processed + 1}: {e}")
            if 'browser' in locals():
                await browser.close()
            break

    # Save Excel file
    filename = f"Helzberg_{current_date}_{time_only}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)
    wb.save(file_path)
    logging.info(f"Data saved to {file_path}")

    # Database operations
    if all_records:
        insert_into_db(all_records)
    update_product_count(len(all_records))

    # Return results
    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    return base64_encoded, filename, file_path