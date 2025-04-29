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
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from io import BytesIO
from openpyxl.drawing.image import Image as XLImage
import httpx
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

def modify_image_url(image_url):
    """Convert a low-res image URL to a higher resolution version for vtexassets.com."""
    if not image_url or image_url == "N/A":
        return image_url

    # Parse the URL into components
    parsed = urlparse(image_url)
    path = parsed.path

    # Replace any image size pattern with the desired high resolution
    modified_path = re.sub(r'(\d{3,4})-(\d{3,4})', '800-1067', path)  # Replaces the size part with 800-1067

    # Replace low-res image size identifier (size-product_list2x-v-1) with high-res variant (size-frontend-large-v-1)
    modified_path = re.sub(r'size-product_list2x-v-1', 'size-frontend-large-v-1', modified_path)

    # Log the modified URL for debugging purposes
    logging.debug(f"Modified URL: {modified_path}")

    # Reassemble the full URL with query params preserved
    modified_url = urlunparse(parsed._replace(path=modified_path))
    return modified_url

def get_alternative_image_url(original_url):
    if original_url.endswith(".jpg"):
        return original_url.replace(".jpg", ".webp")
    return original_url
import traceback
async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    # modified_url = modify_image_url(image_url)

    logging.info(f"Attempting to download image from URL: {image_url}")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/webp,image/*,*/*;q=0.8",
    }
    
    async with httpx.AsyncClient(headers=headers) as client:
        for attempt in range(retries):
            try:
                response = await client.get(image_url)
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logging.error(f"Image not found (404) for {product_name}: {image_url}")
                    # Try alternative format (.webp)
                    alt_url = get_alternative_image_url(image_url)
                    if alt_url != image_url:
                        try:
                            alt_response = await client.get(alt_url)
                            alt_response.raise_for_status()
                            with open(image_full_path.replace(".jpg", ".webp"), "wb") as f:
                                f.write(alt_response.content)
                            return image_full_path.replace(".jpg", ".webp")
                        except Exception as alt_err:
                            logging.warning(f"Alternative image also failed for {product_name}: {alt_url} - {alt_err}")
                    break  # Break on 404 – no point retrying the same URL

                else:
                    logging.warning(f"HTTP error {e.response.status_code} for {product_name}: {image_url}")
                    traceback.print_exc()

            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Network error for {product_name}: {e}")
                traceback.print_exc()

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
            product_cards = await page.wait_for_selector('.product-card', state="attached", timeout=30000)

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

            


async def handle_klenotyaurum(url, max_pages):
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
    filename = f"handle_klenotyaurum_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        
        if page_count == 1:
            current_url = f"{url}"
        else:
            # Otherwise, add the pagination query for multiple pages
            current_url = f"{url}?pageStart=1&paginator-page={page_count}"


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
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))  # Random delay between scrolls
                    current_product_count = await page.locator('div.product-card').count()  # Use product-card class instead of data-testid
                    if current_product_count == prev_product_count:
                        break
                    prev_product_count = current_product_count

                # Final product count log
                products = await page.locator('div.product-card').all()  # Use product-card class to grab all product cards
                logging.info(f"🧾 Total products found on page {page_count}: {len(products)}")



                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        # Locate the product name (ignoring nested <font> tags)
                        product_name = await product.locator("h2.product-name > span").inner_text()
                    except:
                        product_name = "N/A"


                    try:
                        # Locate the price (including nested <font> tags)
                        price = await product.locator("span.info-price-num").inner_text()
                    except:
                        price = "N/A"

                    try:
                        # Locate the second image URL inside the second <picture> element and prioritize the highest resolution image
                        image_url = await product.locator("picture:nth-of-type(1) img").get_attribute("src")
                        if not image_url:
                            # If the main image is not found, fall back to the source URL
                            image_url = await product.locator("picture:nth-of-type(1) source").get_attribute("srcset")
                    except:
                        image_url = "N/A"




                    print(image_url)

                    kt_full_match = re.findall(r"\d+(?:\.\d+)?ct\s*(?:Yellow|White|Rose)?\s*Gold|Gold Plated|Sterling Silver|Platinum|Stainless Steel|Tungsten", product_name, re.IGNORECASE)
                    kt = ", ".join([match.strip() for match in kt_full_match]) if kt_full_match else "N/A"


                    # Extract Diamond Weight (supports "1.85ct", "2ct", "1.50ct", etc.)
                    diamond_weight_match = re.findall(r"\b(\d+(?:\.\d+)?\s*ct)\b", product_name, re.IGNORECASE)
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
