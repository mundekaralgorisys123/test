import os
import time
import logging
import re
import uuid
import base64
import asyncio
from datetime import datetime

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage

import httpx
from playwright.async_api import async_playwright

from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count

# Load environment variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

# Setup paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def modify_image_url(image_url):
    if not image_url or image_url == "N/A":
        return image_url
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)
    return modified_url + query_params


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
                await asyncio.sleep(2)
    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"


async def safe_goto_and_wait(page, url):
    for attempt in range(3):
        try:
            logging.info(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")
            await page.wait_for_selector(".gridBlock.row", timeout=15_000)
            count = await page.eval_on_selector_all(".gridBlock.row > *", "els => els.length")
            logging.info(f"[Success] Product grid loaded with {count} items.")
            return
        except Exception as e:
            logging.warning(f"[Retry {attempt + 1}] Error loading page: {e}")
            await asyncio.sleep(2)
    raise Exception(f"[Error] Failed to load product grid on {url} after retries.")


async def handle_goldsmiths(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Starting Goldsmiths scraping | IP: {ip_address} | URL: {url}")

    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")
    current_page = 1
    current_url = f"{url}?page={current_page}&sort="
    row_counter = 2  # Start from Excel row 2 (after headers)

    records = []
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(PROXY_URL)
        page = await browser.new_page()
        try:
            while current_url and current_page <= max_pages:
                await safe_goto_and_wait(page, current_url)
                log_event(f"Successfully loaded: {current_url}")

                prev_count = 0
                for _ in range(50):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
                    try:
                        await page.wait_for_selector(".productTile", timeout=5000)
                        current_count = await page.locator(".productTile").count()
                        if current_count == prev_count:
                            break
                        prev_count = current_count
                    except:
                        break

                page_title = await page.title()
                wrapper = await page.query_selector("div.gridBlock.row")
                products = await wrapper.query_selector_all("div.productTile") if wrapper else []
                logging.info(f"Total products found: {len(products)}")

                image_tasks = []

                for product in products:
                    try:
                        product_name_el = await product.query_selector("div.productTileName")
                        price_el = await product.query_selector("div.productTilePrice")
                        if not product_name_el or not price_el:
                            continue
                        product_name = (await product_name_el.inner_text()).strip()
                        price = (await price_el.inner_text()).strip()

                        image_url = "N/A"
                        try:
                            image_elements = await product.query_selector_all("img.productListerPrimary")
                            urls = [
                                await img.get_attribute("src") or await img.get_attribute("data-src")
                                for img in image_elements
                            ]
                            urls = [u for u in urls if u and u.startswith("https://")]
                            image_url = urls[0] if urls else "N/A"
                        except Exception as e:
                            logging.warning(f"Image extraction error: {e}")

                        kt_match = re.search(r"(\d{1,2}ct?\s*(?:Yellow|White|Rose)?\s*Gold)", product_name, re.IGNORECASE)
                        kt = kt_match.group(1) if kt_match else "N/A"
                        dia_match = re.search(r"(\d+(?:\.\d+)?\s*ct(?:tw|t\.?w\.?)?)", product_name, re.IGNORECASE)
                        diamond_weight = dia_match.group(1) if dia_match else "N/A"

                        unique_id = str(uuid.uuid4())
                        task = asyncio.create_task(
                            download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                        )
                        image_tasks.append((row_counter, unique_id, task))

                        records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight))
                        sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])
                        row_counter += 1
                    except Exception as e:
                        logging.error(f"Product parsing error: {e}")
                        continue

                for row_num, unique_id, task in image_tasks:
                    image_path = await task
                    if image_path != "N/A":
                        try:
                            img = ExcelImage(image_path)
                            img.width, img.height = 100, 100
                            sheet.add_image(img, f"D{row_num}")
                        except Exception as img_err:
                            logging.warning(f"Failed to insert image: {img_err}")

                    for i, record in enumerate(records):
                        if record[0] == unique_id:
                            records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7])
                            break

                next_button = await page.query_selector("div#pagination-LoadMore")
                if next_button:
                    next_page = await next_button.get_attribute("data-next-page")
                    if next_page:
                        current_page += 1
                        current_url = f"{url}?q=&page={current_page}&sort="
                        logging.info(f"[Next Page] {current_url}")
                        continue
                break
        finally:
            await page.close()
            await browser.close()

    filename = f"handle_goldsmiths_{current_date}_{time_only}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)
    wb.save(file_path)

    with open(file_path, "rb") as f:
        base64_encoded = base64.b64encode(f.read()).decode("utf-8")

    insert_into_db(records)
    update_product_count(len(records))
    logging.info(f"✅ Scraping complete. File saved: {file_path}")

    return base64_encoded, filename, file_path
