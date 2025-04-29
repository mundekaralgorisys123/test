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


async def download_image(session, image_url, product_name, timestamp, image_folder, unique_id):
    if not image_url or image_url == "N/A" or image_url.startswith('data:image'):
        return "N/A"
    
    # Ensure proper URL format
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    
    for attempt in range(3):
        try:
            resp = await session.get(image_url, timeout=10)
            resp.raise_for_status()
            
            # Check if we got a real image (not a placeholder)
            content_type = resp.headers.get('content-type', '')
            if content_type.startswith('image/') and not content_type.startswith('image/gif'):
                with open(image_full_path, "wb") as f:
                    f.write(resp.content)
                return image_full_path
            else:
                logging.warning(f"Invalid image content type: {content_type}")
                return "N/A"
                
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/3 - Error downloading {product_name}: {e}")
            if attempt < 2:  # Don't sleep on last attempt
                await asyncio.sleep(1)
    
    logging.error(f"Failed to download {product_name} after 3 attempts.")
    return "N/A"

async def handle_cushlawhiting(url_page, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Starting scrape for {url_page} from IP: {ip_address}")  # Changed url to url_page

    if not os.path.exists(EXCEL_DATA_PATH):
        os.makedirs(EXCEL_DATA_PATH)

    if not os.path.exists(EXCEL_DATA_PATH):
        os.makedirs(EXCEL_DATA_PATH)

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

    async with httpx.AsyncClient() as session:
        current_page = 1
        all_products = []
        has_more_products = True

        while current_page <= max_pages and has_more_products:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                page = await browser.new_page()

                try:
                    await page.goto(url_page, timeout=120000)
                    await page.wait_for_selector(".grid__item", timeout=30000)
                except Exception as e:
                    logging.warning(f"Failed to load URL {url}: {e}")
                    await browser.close()
                    continue

                # Only click "Load More" after the first page
                try:
                    for i in range(1, current_page):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1)
                        button = await page.query_selector("button#view-more-product")
                        if button and await button.is_visible():
                            await button.scroll_into_view_if_needed()
                            await asyncio.sleep(0.5)
                            await button.click()
                        else:
                            logging.info("No more 'Load More' button found")
                            has_more_products = False
                except Exception as e:
                    logging.warning(f"Error clicking 'Load More': {e}")
                    has_more_products = False

                # Get all current products
                current_products = await page.query_selector_all("div.card-wrapper")
                logging.info(f"Page {current_page}: Found {len(current_products)} products")

                # Only process products we haven't seen before
                new_products = current_products[len(all_products):]
                all_products.extend(new_products)

                print(f"Page {current_page}: Scraping {len(new_products)} new products")
                page_title = await page.title()

                for idx, product in enumerate(new_products):
                    print(f"Processing product {idx + 1}/{len(new_products)}")
                    try:
                        product_name_tag = await product.query_selector("span.card-information__text")
                        product_name = await product_name_tag.inner_text() if product_name_tag else "N/A"
                    except Exception as e:
                        print(f"[Product Name] Error: {e}")
                        product_name = "N/A"

                    try:
                        price_tag = await product.query_selector("span.price-item--regular")
                        price = await price_tag.inner_text() if price_tag else "N/A"
                    except Exception as e:
                        print(f"[Price] Error: {e}")
                        price = "N/A"
                    image_url = "N/A"
                    try:
                        # Select the visible image container
                        media_container = await product.query_selector('div.card__inner')
                        if media_container:
                            # Get all images in the container
                            images = await media_container.query_selector_all('img')
                            
                            # Find the first visible image (not hidden)
                            visible_img = None
                            for img in images:
                                class_list = await img.get_attribute('class') or ''
                                if 'hide-image' not in class_list and 'motion-reduce' in class_list:
                                    visible_img = img
                                    break
                            
                            if visible_img:
                                # First try to get the highest resolution from data-srcset
                                data_srcset = await visible_img.get_attribute('data-srcset')
                                if data_srcset:
                                    # Extract all available sizes and pick the largest one
                                    srcset_parts = [part.strip() for part in data_srcset.split(",")]
                                    largest_url = ""
                                    largest_size = 0
                                    for part in srcset_parts:
                                        if not part:
                                            continue
                                        try:
                                            url, size = part.rsplit(" ", 1)  # Split on last space
                                            size = int(size.replace("w", ""))
                                            if size > largest_size:
                                                largest_size = size
                                                largest_url = url
                                        except Exception as e:
                                            logging.warning(f"Error parsing srcset part: {part} - {e}")
                                    
                                    if largest_url:
                                        image_url = largest_url
                                    else:
                                        # Fallback to data-src if available
                                        image_url = await visible_img.get_attribute('data-src') or await visible_img.get_attribute('src')
                                else:
                                    # No srcset, try regular attributes
                                    image_url = await visible_img.get_attribute('data-src') or await visible_img.get_attribute('src')
                                
                                # Ensure we have a proper URL
                                if image_url and image_url.startswith('//'):
                                    image_url = 'https:' + image_url
                                elif image_url and image_url.startswith('data:image'):
                                    image_url = "N/A"
                            else:
                                image_url = "N/A"
                        else:
                            image_url = "N/A"

                    except Exception as e:
                        print(f"[Image URL] Error: {e}")
                        image_url = "N/A"

                    # Extract Gold Type (e.g., "14K Yellow Gold").
                    gold_type_match = re.findall(r"(\d{1,2}ct\s*(?:Yellow|White|Rose)?\s*Gold|Platinum|Cubic Zirconia)", product_name, re.IGNORECASE)
                    kt = ", ".join(gold_type_match) if gold_type_match else "N/A"

                    # Extract Diamond Weight (supports "1.85ct", "2ct", "1.50ct", etc.)
                    diamond_weight_match = re.findall(r"(\d+(?:\.\d+)?\s*ct)", product_name, re.IGNORECASE)
                    diamond_weight = ", ".join(diamond_weight_match) if diamond_weight_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    task = asyncio.create_task(download_image(session, image_url, product_name, timestamp, image_folder, unique_id))
                    image_tasks.append((len(sheet['A']) + 1, unique_id, task))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight))
                    sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url])
                # Process image downloads and attach them to Excel
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

                await browser.close()
            current_page += 1


        # Save Excel
        filename = f'handle_cushlawhiting_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
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
