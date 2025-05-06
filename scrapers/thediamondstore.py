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
from proxysetup import get_browser_with_proxy_strategy
# Load environment variables from .env file
from functools import partial
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

def modify_image_url(image_url: str, target_width: int = 431) -> str:
    """
    Given a Shopify‑CDN progressive image URL like:
      …/19-F2467_204x.progressive.jpg?v=1741292000
    replace the `<width>x` (e.g. `204x`) with `431x`
    to get the higher‑res version:
      …/19-F2467_431x.progressive.jpg?v=1741292000
    """
    if not image_url or image_url == "N/A":
        return image_url

    # Split off query string, if any
    base, *qs = image_url.split("?", 1)
    query = f"?{qs[0]}" if qs else ""

    # Replace the `_⟨digits⟩x` immediately before `.progressive`
    new_base = re.sub(
        r'_(\d+)x(?=\.progressive)',
        f'_{target_width}x',
        base
    )

    return new_base + query


async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url, target_width=431)
    # print(modified_url)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(retries):
            try:
                # Try high-res version first
                response = await client.get(modified_url)
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path
            except httpx.HTTPStatusError as e:
                # If high-res doesn't exist, fallback to original
                if e.response.status_code == 404 and modified_url != image_url:
                    logging.warning(f"High-res not found for {product_name}, trying original URL.")
                    try:
                        response = await client.get(image_url)
                        response.raise_for_status()
                        with open(image_full_path, "wb") as f:
                            f.write(response.content)
                        return image_full_path
                    except Exception as fallback_err:
                        logging.error(f"Fallback failed for {product_name}: {fallback_err}")
                        break
                else:
                    logging.warning(f"HTTP error on attempt {attempt+1} for {product_name}: {e}")
            except httpx.RequestError as e:
                logging.warning(f"Request error on attempt {attempt+1} for {product_name}: {e}")
    
    logging.error(f"Failed to download image for {product_name} after {retries} attempts.")
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
            product_cards = await page.wait_for_selector(".collection-body", state="attached", timeout=30000)
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


async def extract_image_url(product):
    """Async helper to pull the first valid URL out of srcset/src/data-srcset."""
    img = await product.query_selector("img")
    if not img:
        return "N/A"
    for attr in ("data-srcset", "srcset", "src"):
        raw = await img.get_attribute(attr)
        if raw:
            # srcset may contain multiple comma‑separated entries: "url1 1x, url2 2x"
            first = raw.split(",")[0].strip().split()[0]
            return first if first.startswith("http") else f"https:{first}"
    return "N/A"


async def handle_thediamondstore(url, max_pages):
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
    filename = f"handle_thediamondstore_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0
    current_url = url
    while page_count <= max_pages:
        if page_count > 1:
            if '?' in url:
                current_url = f"{url}&page={page_count}"
            else:
                current_url = f"{url}?page={page_count}"
        logging.info(f"Processing page {page_count}: {current_url}")
        
        # Create a new browser instance for each page
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                product_wrapper = ".collection-body"
                browser, page = await get_browser_with_proxy_strategy(p, current_url, product_wrapper)
                log_event(f"Successfully loaded: {current_url}")

                # Scroll to load all products
                prev_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                    await asyncio.sleep(random.uniform(1, 2))

                    # Count the actual product tiles, not the grid container itself
                    current_count = await page.locator(
                        "#CollectionProductGrid > div.col-6.col-sm-6.col-md-4.col-lg-3.col-xl-3"
                    ).count()

                    if current_count == prev_count:
                        break
                    prev_count = current_count

                # Now grab the wrapper and all product tiles underneath it
                grid = await page.query_selector("div#CollectionProductGrid")
                products = (
                    await grid.query_selector_all("div.col-6.col-sm-6.col-md-4.col-lg-3.col-xl-3")
                    if grid
                    else []
                )

                logging.info(f"Total products found on page {page_count}: {len(products)}")


                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        name_el = await product.query_selector("h4.m-0")
                        product_name = (await name_el.inner_text()).strip() if name_el else "N/A"
                    except:
                        product_name = "N/A"

                    try:
                        price_el = await product.query_selector("span.price--sale")
                        price = (await price_el.inner_text()).strip() if price_el else "N/A"
                    except:
                        price = "N/A"
                        
                        
                    try:
                        image_url = await extract_image_url(product)
                    except:
                        image_url = "N/A"

                    gold_type = re.search(r"\b\d{1,2}(?:ct|K)\s*(?:Yellow|White|Rose)?\s*Gold\b|\bPlatinum\b|\bSilver\b", product_name, re.I)
                    diamond_weight1 = re.findall(r"(\d+(?:\.\d+)?\s*ct)", product_name, re.I)

                    kt = gold_type.group() if gold_type else "N/A"
                    diamond_weight = ", ".join(diamond_weight1) if diamond_weight1 else "N/A"
                    
                    
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
