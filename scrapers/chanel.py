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
from PIL import Image as PILImage  # For image processing
from openpyxl.drawing.image import Image as ExcelImage  # For Excel image insertion
import aiofiles
from utils import get_public_ip, log_event, sanitize_filename
from dotenv import load_dotenv
from database import insert_into_db
from limit_checker import update_product_count

import httpx
from urllib.parse import urlparse
from PIL import Image
import io

load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')


async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url.strip().lower() == "n/a":
        return "N/A"

    try:
        parsed_url = urlparse(image_url)
        if not all([parsed_url.scheme, parsed_url.netloc]):
            logging.error(f"Invalid URL format for {product_name}: {image_url}")
            return "N/A"
    except Exception as e:
        logging.error(f"URL parsing failed for {product_name}: {str(e)}")
        return "N/A"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8"
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=30.0)) as client:
        for attempt in range(1, retries + 1):
            try:
                response = await client.get(image_url, headers=headers, follow_redirects=True)
                
                if response.status_code != 200:
                    raise httpx.HTTPStatusError(f"Bad status code: {response.status_code}", request=response.request, response=response)

                content_type = response.headers.get("Content-Type", "")
                if "image" not in content_type:
                    raise ValueError(f"Unexpected content type: {content_type}")

                # Determine extension
                if "webp" in content_type:
                    extension = ".jpg"  # we'll convert
                elif "jpeg" in content_type or "jpg" in content_type:
                    extension = ".jpg"
                elif "png" in content_type:
                    extension = ".png"
                else:
                    extension = ".jpg"  # fallback
                
                image_filename = f"{unique_id}_{timestamp}{extension}"
                image_full_path = os.path.join(image_folder, image_filename)

                if "webp" in content_type:
                    # Convert webp to jpg
                    image = Image.open(io.BytesIO(response.content)).convert("RGB")
                    await asyncio.to_thread(image.save, image_full_path, format="JPEG", quality=90)
                else:
                    async with aiofiles.open(image_full_path, "wb") as f:
                        await f.write(response.content)

                if os.path.exists(image_full_path) and os.path.getsize(image_full_path) > 0:
                    logging.info(f"Successfully downloaded {product_name}")
                    return image_full_path
                raise IOError("Empty file or write failure")

            except (httpx.RequestError, httpx.HTTPStatusError, IOError, ValueError) as e:
                logging.warning(
                    f"Retry {attempt}/{retries} - Error downloading {product_name}: "
                    f"{type(e).__name__}: {str(e)} | URL: {image_url}"
                )

                if attempt < retries:
                    await asyncio.sleep(2 ** (attempt - 1))

                if isinstance(e, httpx.HTTPStatusError):
                    logging.debug(f"Response headers: {e.response.headers}")
                    logging.debug(f"Response text: {e.response.text[:200]}")

    logging.error(f"Permanent failure downloading {product_name} after {retries} attempts")
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
            product_cards = await page.wait_for_selector(".pdp-grid__main.pdp-desktop", state="attached", timeout=30000)

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

            



async def handle_chanel(url, max_pages):
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
    filename = f"handle_chanel_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        current_url = f"{url}/page-{page_count}/"

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
                prev_product_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))  # Random delay between scrolls
                    current_product_count = await page.locator('.product-grid__item').count()
                    if current_product_count == prev_product_count:
                        break
                    prev_product_count = current_product_count


                products = await page.query_selector_all(".product-grid__item.js-product-edito")
                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    try:
                        product_name_tag = await product.query_selector('span[data-test="lnkProductPLP_BySKU"]')
                        if product_name_tag:
                            product_name = await product_name_tag.inner_text()
                        else:
                            product_name = "N/A"
                            print("Product name element not found")
                    except Exception as e:
                        print(f"Error fetching product name: {str(e)}")
                        product_name = "N/A"

                    try:
                        price_tag = await product.query_selector('p[data-test="lblProductPrice_PLP"]')
                        if price_tag:
                            price = (await price_tag.inner_text()).strip()
                            price = price.replace('€', '').replace('$', '').replace('₹', '').strip()
                        else:
                            price = "N/A"
                            print("Price element not found")
                    except Exception as e:
                        print(f"Error fetching price: {str(e)}")
                        price = "N/A"

                    try:
                        image_tag = await product.query_selector('img')
                        if image_tag:
                            image_src = await image_tag.get_attribute('src')
                            if not image_src:
                                image_src = await image_tag.get_attribute('data-src')
                            if not image_src:
                                image_srcset = await image_tag.get_attribute('srcset')
                                if image_srcset:
                                    # Get the highest resolution image from srcset (usually last item)
                                    image_src = image_srcset.split(',')[-1].split(' ')[0]
                            image_url = image_src.strip() if image_src else "N/A"
                        else:
                            image_url = "N/A"
                    except Exception as e:
                        print(f"Error fetching image URL: {str(e)}")
                        image_url = "N/A"

                    print(image_url)

                    try:
                        description_tag = await product.query_selector('span[data-test="lblProductShrotDescription_PLP"]')
                        if description_tag:
                            description = (await description_tag.inner_text()).strip()
                        else:
                            description = "N/A"
                            print("Description element not found")
                    except Exception as e:
                        print(f"Error fetching description: {str(e)}")
                        description = "N/A"




                    try:
                        kt_match = re.search(r'\d{1,2}K', description)
                        kt = kt_match.group() if kt_match else "Not found"
                    except Exception as e:
                        print(f"Error extracting kt from description: {str(e)}")
                        kt = "Not found"
                        
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
                        # Download image asynchronously with timeout
                        image_path = await asyncio.wait_for(task, timeout=60)
                        
                        if image_path != "N/A":
                            try:
                                # Open the image using PIL for processing
                                pil_image = PILImage.open(image_path)
                                
                                # Resize the image if needed
                                pil_image = pil_image.resize((100, 100))

                                # Save the processed image temporarily to a new file
                                temp_image_path = f"temp_{unique_id}.jpg"
                                pil_image.save(temp_image_path, format="JPEG", quality=90)

                                # Use openpyxl's Image to add the image to Excel
                                img = ExcelImage(temp_image_path)  # This uses openpyxl.drawing.image.Image for Excel
                                img.width, img.height = 100, 100  # Resize for Excel
                                sheet.add_image(img, f"D{row_num}")  # Insert the image into the Excel sheet
                                
                            except Exception as img_error:
                                logging.error(f"Error adding image to Excel: {img_error}")
                                image_path = "N/A"

                        # Update the records with the image path
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
