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
from utils import get_public_ip, log_event, sanitize_filename
from dotenv import load_dotenv
from database import insert_into_db
from limit_checker import update_product_count
import aiohttp
from io import BytesIO
from openpyxl.drawing.image import Image as XLImage
import httpx
# Load environment variables from .env file
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')



def upgrade_to_high_res_url(image_url):
    if not image_url or image_url == "N/A":
        return image_url

    base_url = image_url.split("?")[0]
    return re.sub(r'_\d+X\d+(?=\.jpg$)', '_1600X1600', base_url)


async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)

    high_res_url = upgrade_to_high_res_url(image_url)  # assume this transforms to higher quality version

    async with httpx.AsyncClient(timeout=10.0) as client:
        urls_to_try = [high_res_url, image_url]  # try high-res first, then fallback to original
        for url in urls_to_try:
            for attempt in range(retries):
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    with open(image_full_path, "wb") as f:
                        f.write(response.content)
                    return image_full_path
                except httpx.RequestError as e:
                    logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading from {url}: {e}")
                except httpx.HTTPStatusError as e:
                    logging.warning(f"Retry {attempt + 1}/{retries} - HTTP error from {url}: {e}")
            logging.info(f"Switching to fallback URL after {retries} failed attempts for {url}")
    
    logging.error(f"Failed to download image for {product_name} after trying both URLs.")
    return "N/A"

def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    time.sleep(random.uniform(min_sec, max_sec))



async def safe_goto_and_wait(page, url, retries=3):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")


            # Wait for the selector with a longer timeout
            product_cards = await page.wait_for_selector(".gallery-grid-container--vJWMdFUhYMhp1TP3jIfs", state="attached", timeout=30000)

            # Optionally validate at least 1 is visible (Playwright already does this)
            if product_cards:
                print("[Success] Product cards loaded.")
                return
        except Error as e:
            logging.error(f"Error navigating to {url} on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                logging.info("Retrying after waiting a bit...")
                random_delay(1, 3)  # Add a delay before retrying
            else:
                logging.error(f"Failed to navigate to {url} after {retries} attempts.")
                raise
        except TimeoutError as e:
            logging.warning(f"TimeoutError on attempt {attempt + 1} navigating to {url}: {e}")
            if attempt < retries - 1:
                logging.info("Retrying after waiting a bit...")
                random_delay(1, 3)  # Add a delay before retrying
            else:
                logging.error(f"Failed to navigate to {url} after {retries} attempts.")
                raise

            
# Scroll to bottom of page to load all products
async def scroll_to_bottom(page):
    last_height = await page.evaluate("document.body.scrollHeight")
    while True:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1, 3))  # Random delay between scrolls
        
        # Check if we've reached the bottom
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
            


async def handle_bluenile(url, max_pages):
    
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
    filename = f"handle_bluenile_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    prev_prod_cout = 0
    load_more_clicks = 1
    while load_more_clicks <= max_pages:
        
        logging.info(f"Processing page {load_more_clicks}: {url}")
        
        # Create a new browser instance for each page
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                context = await browser.new_context()
                
                # Configure timeouts for this page
                page = await context.new_page()
                page.set_default_timeout(120000)  # 2 minute timeout
                
                await safe_goto_and_wait(page, url)
                log_event(f"Successfully loaded: {url}")

                # Scroll to load all products
                await scroll_to_bottom(page)

                # Now query products using Blue Nile's actual DOM
                product_container = await page.wait_for_selector("#data-page-container", timeout=30000)
                products = await product_container.query_selector_all("div[class^='item--']")
                max_prod = len(products)
                logging.info(f"New products found: {max_prod}")
                print(f"New products found: {max_prod}")
                
                products = products[prev_prod_cout: min(max_prod, prev_prod_cout + 16)]
                prev_prod_cout += len(products)

                if len(products) == 0:
                    log_event("No new products found, stopping the scraper.")
                    break

                logging.info(f"New products found: {len(products)}")
                print(f"New products found: {len(products)}")
                # products = await page.query_selector_all("div.item--BtojO4WSSsxPN6lzc96B")

                # products =  await page.query_selector("div.item--BtojO4WSSsxPN6lzc96B").all()
                logging.info(f"Total products found on page {load_more_clicks}: {len(products)}")


                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        product_name_el = await product.query_selector("div.itemTitle--U5mJCpztfNqClWjA0gnb span")
                        product_name = await product_name_el.inner_text() if product_name_el else "N/A"
                    except:
                        product_name = "N/A"

                    try:
                        # Prefer sale price if available
                        sale_price_el = await product.query_selector("div.price--D59bW_owiHOgefBGjZBy")
                
                        if sale_price_el:
                            price = await sale_price_el.inner_text()
                        
                        else:
                            price = "N/A"
                    except:
                        price = "N/A"

                    try:
                        # Lifestyle image usually looks more styled, prefer it if present
                        await product.scroll_into_view_if_needed()
                        image_el = await product.query_selector("div.imageContainer--UuMEUHM2d6Z6l3MEk8RD img")
                        if not image_el:
                            image_el = await product.query_selector("div.imageContainer--UuMEUHM2d6Z6l3MEk8RD img")
                        image_url = await image_el.get_attribute("src") if image_el else "N/A"
                    except:
                        image_url = "N/A"

                    gold_type_match = re.findall(r"(\d{1,2}ct\s*(?:Yellow|White|Rose)?\s*Gold|Platinum)", product_name, re.IGNORECASE)
                    kt = ", ".join(gold_type_match) if gold_type_match else "N/A"

                    # Extract Diamond Weight (supports "1.85ct", "2ct", "1.50ct", etc.)
                    diamond_weight_match = re.findall(r"(\d+(?:\.\d+)?\s*ct)", product_name, re.IGNORECASE)
                    diamond_weight = ", ".join(diamond_weight_match) if diamond_weight_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight))
                    sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])

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

                load_more_clicks += 1
                all_records.extend(records)
                wb.save(file_path)
                
        except Exception as e:
            logging.error(f"Error during scraping: {str(e)}")
            wb.save(file_path)
        finally:
            if page: await page.close()
            if browser: await browser.close()

    wb.save(file_path)
    log_event(f"Data saved to {file_path}")
    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path