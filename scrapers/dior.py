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
from playwright.async_api import async_playwright, TimeoutError, Error
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
import mimetypes
from proxysetup import get_browser_with_proxy_strategy


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

mimetypes.add_type('image/webp', '.webp')

async def extract_best_image_url(product_element):
    try:
        # Simply get the first img element and its src
        img_element = await product_element.query_selector("img")
        if img_element:
            img_src = await img_element.get_attribute("src")
            if img_src:
                return img_src 
        return None
    except Exception as e:
        log_event(f"Error extracting image URL: {e}")
        return None

# Async image downloader
async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"  # Always save as JPG
    image_full_path = os.path.join(image_folder, image_filename)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(retries):
            try:
                response = await client.get(image_url)
                response.raise_for_status()
                img_data = response.content
                
                # Convert WEBP to JPG if needed
                if image_url.lower().endswith('.webp'):
                    try:
                        img = PILImage.open(BytesIO(img_data))
                        if img.format == 'WEBP':
                            buffer = BytesIO()
                            img.convert('RGB').save(buffer, format="JPEG", quality=85)
                            img_data = buffer.getvalue()
                    except Exception as e:
                        log_event(f"Error converting WEBP to JPG: {e}")
                        continue
                
                with open(image_full_path, "wb") as f:
                    f.write(img_data)
                return image_full_path
                
            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")
    
    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"

# Human-like delay
def random_delay(min_sec=1, max_sec=3):
    time.sleep(random.uniform(min_sec, max_sec))

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


# Main scraper function
async def handle_dior(url, max_pages=None):
    
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} from IP: {ip_address}")

    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath", "Additional Info"]
    sheet.append(headers)

    all_records = []
    filename = f"handle_dior_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)
    browser = None
    page = None
    
    try:
        async with async_playwright() as p:
            product_wrapper = '.MuiGrid-root.MuiGrid-container'
            browser, page = await get_browser_with_proxy_strategy(p, url, product_wrapper)
            log_event(f"Successfully loaded: {url}")

            # Scroll to load all items
            await scroll_to_bottom(page)
            
            page_title = await page.title()
            current_date = datetime.now().strftime("%Y-%m-%d")
            time_only = datetime.now().strftime("%H.%M")

            # Get all product tiles
            product_tiles = await page.query_selector_all("li.MuiGrid-item")
            logging.info(f"Total products found: {len(product_tiles)}")
            print(f"Total products found: {len(product_tiles)}")
            records = []
            image_tasks = []
            
            for row_num, product in enumerate(product_tiles, start=len(sheet["A"]) + 1):
                try:
                    # Extract product name
                    name_tag = await product.query_selector(".MuiTypography-label-m-medium")
                    product_name = (await name_tag.inner_text()).strip() if name_tag else "N/A"
                except Exception:
                    product_name = "N/A"

                try:
                    # Extract price
                    price_tag = await product.query_selector(".card-legend-price")
                    price = (await price_tag.inner_text()).strip() if price_tag else "N/A"
                except Exception:
                    price = "N/A"

                try:
                    # Extract description (which contains material info)
                    desc_tag = await product.query_selector(".MuiTypography-label-m-regular")
                    description = (await desc_tag.inner_text()).strip() if desc_tag else "N/A"
                except Exception:
                    description = "N/A"

                image_url = "N/A"
                try:
                    image_url = await extract_best_image_url(product) or "N/A"
                except Exception as e:
                    log_event(f"Error getting image URL: {e}")
                    image_url = "N/A"

                # Extract gold type (kt) from description
                try:
                    # Extract metal type (e.g. “White Gold”)
                    metal_tag = await product.query_selector(
                        "span.MuiTypography-label-m-regular, span.DS-Typography"
                    )
                    kt = (await metal_tag.inner_text()).strip() if metal_tag else "N/A"
                except Exception:
                    kt = "N/A"


                # Extract diamond weight from description
                diamond_weight_pattern = r"\b\d+(\.\d+)?\s*(?:ct|tcw)\b"
                diamond_weight_match = re.search(diamond_weight_pattern, description, re.IGNORECASE)
                diamond_weight = diamond_weight_match.group() if diamond_weight_match else "N/A"
                
                additional_info_str = "N/A "
                
              

                unique_id = str(uuid.uuid4())
                if image_url and image_url != "N/A":
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight,additional_info_str))
                sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url,additional_info_str])
            
            # Process image downloads
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
                            records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7], record[8])
                            break
                except asyncio.TimeoutError:
                    logging.warning(f"Image download timed out for row {row_num}")

            all_records.extend(records)
            wb.save(file_path)
            
    except Exception as e:
        logging.error(f"Error during scraping: {str(e)}")
        wb.save(file_path)
    finally:
        if page: await page.close()
        if browser: await browser.close()

    if not all_records:
        return None, None, None

    # Save the workbook
    wb.save(file_path)
    log_event(f"Data saved to {file_path}")

    # Encode the file in base64
    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    # Insert data into the database and update product count
    insert_into_db(all_records)
    update_product_count(len(all_records))

    # Return necessary information
    return base64_encoded, filename, file_path