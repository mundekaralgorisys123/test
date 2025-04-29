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
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from utils import get_public_ip, log_event, sanitize_filename
from dotenv import load_dotenv
from database import insert_into_db
from limit_checker import update_product_count
import aiohttp
from io import BytesIO
from openpyxl.drawing.image import Image as XLImage
import httpx
# Load environment variables from .env file
from functools import partial
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

async def download_and_resize_image(session, image_url):
    try:
        async with session.get(build_high_res_url(image_url), timeout=10) as response:
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

def build_high_res_url(image_url, width="1500"):
    if not image_url or image_url == "N/A":
        return image_url

    # Add scheme if it's a protocol-relative URL (starts with //)
    if image_url.startswith("//"):
        image_url = "https:" + image_url

    # Parse the URL
    parsed = urlparse(image_url)
    query_dict = parse_qs(parsed.query)
    query_dict["width"] = [width]  # Overwrite or insert width

    # Rebuild the URL
    new_query = urlencode(query_dict, doseq=True)
    high_res_url = urlunparse(parsed._replace(query=new_query))

    return high_res_url

async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)

    high_res_url = build_high_res_url(image_url, width="1500")  # Make sure we're aiming for a high-res image.
    fallback_url = image_url  # In case the high-res image isn't found.

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Try HEAD request to check if high-res image exists
        try:
            head_response = await client.head(high_res_url)
            if head_response.status_code == 200:
                image_to_download = high_res_url  # High-res image found
            else:
                logging.warning(f"High-res image not found for {product_name}, using fallback.")
                image_to_download = fallback_url  # Fallback to original image
        except Exception as e:
            logging.warning(f"Could not check high-res image. Falling back. Reason: {e}")
            image_to_download = fallback_url  # Fallback if HEAD request fails

        # Attempt to download the image
        for attempt in range(retries):
            try:
                response = await client.get(image_to_download)
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path  # Return the downloaded image path
            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")

    logging.error(f"Failed to download {product_name} after {retries} attempts.")
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
            product_cards = await page.wait_for_selector(".snize-search-results-main-content", state="attached", timeout=30000)

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

            



async def handle_medleyjewellery(url, max_pages):
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
    filename = f"handle_medleyjewellery_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        current_url = f"{url}?page={page_count}"
        logging.info(f"Processing page {page_count}: {current_url}")
        
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
                
                await safe_goto_and_wait(page, current_url)
                log_event(f"Successfully loaded: {current_url}")

                # Scroll to load all products
                # Scroll to load all products
                prev_product_count = 0
                for _ in range(10):  # You can adjust the range if needed
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # Scroll to the bottom
                    await page.wait_for_timeout(2000)  # Wait for 2 seconds for the page to load
                    current_product_count = await page.locator('.snize-item').count()

                    # If no new products are found, exit the loop
                    if current_product_count == prev_product_count:
                        break

                    prev_product_count = current_product_count

                # Find the product wrapper
                product_wrapper = await page.query_selector("ul.snize-search-results-content.clearfix")

                # Query products inside wrapper if it exists
                products = await product_wrapper.query_selector_all("li.snize-product[data-original-product-id]") if product_wrapper else []


                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        # Wait for the title element to be attached (use an appropriate timeout)
                        product_name_element = await product.query_selector("span.snize-title")
                        if product_name_element:
                            product_name = await product_name_element.inner_text()
                        else:
                            product_name = "N/A"  # Fallback in case the element is not found
                    except Exception as e:
                        # Log the error if something goes wrong
                        logging.error(f"Error extracting product name: {e}")
                        product_name = "N/A"

                    try:
                        # Wait for the price element to be available
                        price_element = await product.query_selector("span.snize-price")
                        if price_element:
                            price = await price_element.inner_text()
                            price = price.strip()  # Remove extra spaces if any
                        else:
                            price = "N/A"  # Fallback if the price element is not found
                    except Exception as e:
                        # Log the error if something goes wrong
                        logging.error(f"Error extracting price: {e}")
                        price = "N/A"



                    try:
                        # Wait for the image element to be available
                        image_element = await product.query_selector("span.snize-thumbnail img")
                        if image_element:
                            image_url = await image_element.get_attribute("src")
                        else:
                            image_url = "N/A"  # Fallback if no image is found
                    except Exception as e:
                        # Log the error if something goes wrong
                        logging.error(f"Error extracting image URL: {e}")
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
