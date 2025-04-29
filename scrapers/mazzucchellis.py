import asyncio
import re
import os
import uuid
import logging
import base64
from datetime import datetime
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from flask import Flask
from dotenv import load_dotenv
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
import httpx
from playwright.async_api import async_playwright, TimeoutError

# Load .env variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

def modify_image_url(image_url):
    if not image_url or image_url == "N/A":
        return image_url
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)
    return modified_url + query_params

async def download_image(session, image_url, product_name, timestamp, image_folder, unique_id):
    if not image_url or image_url == "N/A":
        return "N/A"
    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url)
    for attempt in range(3):
        try:
            resp = await session.get(modified_url, timeout=10)
            resp.raise_for_status()
            with open(image_full_path, "wb") as f:
                f.write(resp.content)
            return image_full_path
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/3 - Error downloading {product_name}: {e}")
    logging.error(f"Failed to download {product_name} after 3 attempts.")
    return "N/A"

async def handle_mazzucchellis(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Starting scrape for {url} from IP: {ip_address}")

    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")

    seen_ids = set()
    records = []
    image_tasks = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(PROXY_URL)

        async with httpx.AsyncClient() as session:
            context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(url, timeout=120000)
            except Exception as e:
                logging.warning(f"Failed to load URL {url}: {e}")
                await context.close()
                await browser.close()
                return "", "", ""

            page_title = await page.title()

            load_more_clicks = 1
            previous_count = 0
            while load_more_clicks <= max_pages:
                await asyncio.sleep(2)
                all_products = await page.query_selector_all(".nn-product-preview")
                total_products = len(all_products)
                new_products = all_products[previous_count:]
                logging.info(f"Page {load_more_clicks}: Total = {total_products}, New = {len(new_products)}")
                previous_count = total_products
                
                print(f"Page {load_more_clicks}: Scraping {len(new_products)} new products.")

                for product in new_products:
                    product_id = await product.get_attribute("data-productcode") or str(uuid.uuid4())
                    if product_id in seen_ids:
                        continue
                    seen_ids.add(product_id)

                    try:
                        product_name_tag = await product.query_selector('h3 a')
                        product_name = await product_name_tag.inner_text() if product_name_tag else "N/A"
                    except:
                        product_name = "N/A"

                    try:
                        price_tag = await product.query_selector('.nn-price-current')
                        price = await price_tag.inner_text() if price_tag else "N/A"
                    except:
                        price = "N/A"

                    try:
                        image_tag = await product.query_selector('img')
                        image_url = await image_tag.get_attribute('src') if image_tag else "N/A"
                    except:
                        image_url = "N/A"

                    kt_match = re.search(r"\b\d{1,2}K\s*(?:White|Yellow|Rose)?\s*Gold\b|\bPlatinum\b|\bSilver\b", product_name, re.IGNORECASE)
                    kt = kt_match.group() if kt_match else "Not found"

                    diamond_match = re.search(r"\b(\d+(\.\d+)?)\s*(?:ct|ctw|carat)\b", product_name, re.IGNORECASE)
                    diamond_weight = f"{diamond_match.group(1)} ct" if diamond_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    task = asyncio.create_task(download_image(session, image_url, product_name, timestamp, image_folder, unique_id))
                    image_tasks.append((len(sheet['A']) + 1, unique_id, task))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight))
                    sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])

                try:
                    load_more_button = page.locator("button.btn.btn-outline-primary.btn-filter-load-more.btn-loader")
                    if await load_more_button.is_visible():
                        await load_more_button.click()
                        load_more_clicks += 1
                    else:
                        break
                except Exception as e:
                    logging.warning(f"Load more button error: {e}")
                    break

            # Handle images
            for row, unique_id, task in image_tasks:
                image_path = await task
                if image_path != "N/A":
                    img = Image(image_path)
                    img.width, img.height = 100, 100
                    sheet.add_image(img, f"D{row}")
                for i, record in enumerate(records):
                    if record[0] == unique_id:
                        records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7])
                        break

            await context.close()
        await browser.close()

    filename = f'handle_mazzucchellis_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
    file_path = os.path.join(EXCEL_DATA_PATH, filename)
    wb.save(file_path)
    log_event(f"Data saved to {file_path} | IP: {ip_address}")

    if records:
        insert_into_db(records)
    else:
        logging.info("No data to insert into the database.")

    update_product_count(len(seen_ids))

    with open(file_path, "rb") as f:
        base64_encoded = base64.b64encode(f.read()).decode("utf-8")

    return base64_encoded, filename, file_path
