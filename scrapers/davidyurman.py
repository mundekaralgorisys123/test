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
    if not image_url or image_url == "N/A":
        return "N/A"
    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)

    for attempt in range(3):
        try:
            resp = await session.get(image_url, timeout=10)
            resp.raise_for_status()
            with open(image_full_path, "wb") as f:
                f.write(resp.content)
            return image_full_path
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/3 - Error downloading {product_name}: {e}")
    logging.error(f"Failed to download {product_name} after 3 attempts.")
    return "N/A"

async def handle_davidyurman(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Starting scrape for {url} from IP: {ip_address}")

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
    page_size = 32  # Default page size
    base_url = None

    async with httpx.AsyncClient() as session:
        page_number = 1
        total_processed = 0

        while page_number <= max_pages:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                page = await browser.new_page()

                try:
                    if base_url is None:
                        # First page load
                        await page.goto(url, timeout=120000)
                        await page.wait_for_selector('div.tile-item', timeout=30000)
                        
                        # Extract base URL and page size
                        grid_footer = await page.query_selector('div.grid-footer')
                        if grid_footer:
                            permalink = await page.eval_on_selector('.permalink', 'el => el.value')
                            base_url = re.sub(r'&start=\d+&sz=\d+', '', permalink)
                            page_size = int(float(await grid_footer.get_attribute('data-page-size')))
                    else:
                        # Construct paginated URL
                        start = (page_number - 1) * page_size
                        paginated_url = f"{base_url}&start={start}&sz={page_size}"
                        await page.goto(paginated_url, timeout=120000)
                        await page.wait_for_selector('div.tile-item', timeout=30000)

                    # Extract product items
                    all_products = await page.query_selector_all("div.tile-item")
                    print(f"Page {page_number}: Found {len(all_products)} products")

                    for product in all_products:
                        try:
                            try:
                                product_info_tag = await product.query_selector('span.primary-title')
                                if product_info_tag:
                                    full_text = await product_info_tag.inner_text()
                                    product_name = full_text.strip()
                                else:
                                    product_name = "N/A"
                            except Exception as e:
                                print(f"Error fetching product name: {e}")
                                product_name = "N/A"


                            try:
                                # Use a more specific CSS selector to find the price element
                                price_tag = await product.query_selector('span.product-tile-price span.sales span.value')
                                
                                if price_tag:
                                    price = await price_tag.inner_text()  # Extract the price text
                                else:
                                    price = "N/A"  # Handle the case when no price is found
                            except Exception as e:
                                print(f"Error fetching price: {e}")
                                price = "N/A"  # Handle any exceptions by assigning "N/A" to the price


                            try:
                                # Use the correct CSS selector to find the image tag
                                image_url_tag = await product.query_selector("img[itemprop='image']")
                                
                                # Check if the image tag exists
                                if image_url_tag:
                                    image_url = await image_url_tag.get_attribute("src")
                                else:
                                    image_url = "N/A"  # In case the image tag is not found
                            except Exception as e:
                                print(f"Error fetching image URL: {e}")  # Detailed error message
                                image_url = "N/A"  # Default value if error occurs
                            
                            if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                                print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                                continue    

                            # Metadata extraction
                            kt_match = re.search(r"\b\d{1,2}K\s*(?:White|Yellow|Rose)?\s*Gold\b|\bPlatinum\b|\bSilver\b", product_name, re.IGNORECASE)
                            kt = kt_match.group() if kt_match else "Not found"
                            
                            diamond_match = re.search(r"\b(\d+(\.\d+)?)\s*(?:ct|ctw|carat)\b", product_name, re.IGNORECASE)
                            diamond_weight = f"{diamond_match.group(1)} ct" if diamond_match else "N/A"

                            unique_id = str(uuid.uuid4())
                            task = asyncio.create_task(
                                download_image(session, image_url, product_name, timestamp, image_folder, unique_id)
                            )
                            image_tasks.append((len(sheet['A']) + 1, unique_id, task))

                            records.append((unique_id, current_date, await page.title(), product_name, None, kt, price, diamond_weight))
                            sheet.append([current_date, await page.title(), product_name, None, kt, price, diamond_weight, time_only, image_url])
                            seen_ids.add(unique_id)
                            
                        except Exception as e:
                            logging.error(f"Error processing product: {e}")
                            continue

                    # Process images
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

                    page_number += 1
                    total_processed += len(all_products)

                except TimeoutError:
                    logging.error(f"Timeout occurred on page {page_number}")
                    break
                except Exception as e:
                    logging.error(f"Critical error on page {page_number}: {e}")
                    break
                finally:
                    await browser.close()

        # Final save operations
        filename = f'davidyurman_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
        file_path = os.path.join(EXCEL_DATA_PATH, filename)
        wb.save(file_path)
        log_event(f"Scraped {total_processed} products | Saved to {file_path}")

        if records:
            insert_into_db(records)
        update_product_count(len(seen_ids))

        with open(file_path, "rb") as f:
            base64_encoded = base64.b64encode(f.read()).decode("utf-8")

        return base64_encoded, filename, file_path
