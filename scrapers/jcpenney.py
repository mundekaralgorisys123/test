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

def modify_image_url(image_url):
    """Enhance Macy's image URL to get higher resolution version"""
    if not image_url or image_url == "N/A":
        return image_url

    modified_url = re.sub(r'wid=\d+', 'wid=1200', image_url)
    modified_url = re.sub(r'hei=\d+', 'hei=1200', modified_url)
    modified_url = re.sub(r'qlt=[^&]+', 'qlt=95', modified_url)
    
    return modified_url


async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url)  # High-res version

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

async def scroll_and_wait(page):
    """Scroll down to load lazy-loaded products."""
    previous_height = await page.evaluate("document.body.scrollHeight")
    await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
    new_height = await page.evaluate("document.body.scrollHeight")
    return new_height > previous_height  # Returns True if more content is loaded

async def safe_goto_and_wait(page, url, retries=3):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            await page.goto(url, timeout=180_000, wait_until="domcontentloaded")


            # Wait for the selector with a longer timeout
            product_cards = await page.wait_for_selector("li[data-automation-id^='list-item']", state="attached", timeout=30000)

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

            



async def handle_jcpenney(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} from IP: {ip_address}, max_pages: {max_pages}")

    # Prepare directories and files
    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    # Create workbook with Additional Info column
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Material", "Price", 
               "Size/Weight", "Time", "ImagePath", "Additional Info"]
    sheet.append(headers)

    all_records = []
    filename = f"JCPenney_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    async with async_playwright() as p:
        while page_count <= max_pages:
            current_url = f"{url}&page={page_count}"
            logging.info(f"Processing page {page_count}: {current_url}")
            
            browser = None
            page = None
            try:
                product_wrapper = "li[data-automation-id^='list-item']"
                browser, page = await get_browser_with_proxy_strategy(p, current_url, product_wrapper)

                log_event(f"Successfully loaded: {current_url}")

                # Scroll to load all products
                prev_product_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))
                    current_product_count = await page.locator("li[data-automation-id^='list-item']").count()
                    if current_product_count == prev_product_count:
                        break
                    prev_product_count = current_product_count

                product_selector = 'ul[data-automation-id="gallery-product-list"] > li[data-automation-id^="list-item-"]'
                products = await page.locator(product_selector).all()
                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    additional_info = []
                    
                    try:
                        # Product Name
                        product_name_tag = product.locator('a[data-automation-id="product-title"]')
                        product_name = await product_name_tag.text_content() if await product_name_tag.count() > 0 else "N/A"
                        product_name = product_name.strip() if product_name else "N/A"
                    except:
                        product_name = "N/A"

                    # Price handling
                    price_info = []
                    try:
                        # Current price
                        current_price_loc = product.locator('span.DXCCO._2Bk5a.wrap, span.DXCCO.wrap, span.k26R9')
                        if await current_price_loc.count() > 0:
                            current_price = await current_price_loc.first.text_content()
                            current_price = current_price.strip().split("(")[0] if current_price else "N/A"
                            price_info.append(current_price)
                        
                        # Original price
                        original_price_loc = product.locator('strike, span.H-M5g')
                        if await original_price_loc.count() > 0:
                            original_price = await original_price_loc.first.text_content()
                            original_price = original_price.strip() if original_price else "N/A"
                            if original_price and original_price != "N/A" and original_price != current_price:
                                price_info.append(original_price)
                                
                                # Calculate discount percentage
                                try:
                                    current_num = float(re.sub(r'[^\d.]', '', current_price))
                                    original_num = float(re.sub(r'[^\d.]', '', original_price))
                                    discount_pct = round((1 - (current_num / original_num)) * 100)
                                    additional_info.append(f"Discount: {discount_pct}%")
                                except:
                                    pass
                        
                        # Check for flash sale
                        flash_sale_loc = product.locator('p.BfDPx[data-automation-id="at-price-marketing-label"]')
                        if await flash_sale_loc.count() > 0:
                            flash_text = await flash_sale_loc.text_content()
                            additional_info.append(f"Promo: {flash_text.strip()}" if flash_text else "")
                    except Exception as e:
                        logging.warning(f"Error getting price info: {str(e)}")
                        price_info = ["N/A"]
                    
                    price = " | ".join(price_info) if price_info else "N/A"

                    # Image URL
                    try:
                        images = await product.locator("img").all()
                        image_urls = []
                        for img in images:
                            src = await img.get_attribute("data-src") or await img.get_attribute("src")
                            if src:
                                full_url = f"https:{src}" if src.startswith("//") else src
                                image_urls.append(full_url)
                        image_url = image_urls[0] if image_urls else "N/A"
                    except:
                        image_url = "N/A"

                    # Material Type
                    material = "N/A"
                    try:
                        material_match = re.search(r"\b(Sterling Silver|Gold|Platinum|Titanium)\b", product_name, re.IGNORECASE)
                        material = material_match.group() if material_match else "N/A"
                    except:
                        pass
                    if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                        print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                        continue

                    # Size/Weight
                    size_weight = "N/A"
                    try:
                        # Fixed regex pattern
                        size_match = re.search(r"(\d+(?:\.\d+)?(?:[-/]\d+)?\s*(?:ct|inch|cm|mm)\b)", product_name, re.IGNORECASE)
                        size_weight = size_match.group() if size_match else "N/A"
                    except Exception as e:
                        logging.warning(f"Error extracting size/weight: {str(e)}")

                    # Additional product info
                    try:
                        # Check for ratings
                        rating_loc = product.locator('div[data-automation-id="productCard-automation-rating"]')
                        if await rating_loc.count() > 0:
                            rating_text = await rating_loc.text_content()
                            rating_clean = " ".join(rating_text.split()) if rating_text else ""
                            if rating_clean:
                                additional_info.append(f"Rating: {rating_clean}")
                    except:
                        pass

                    try:
                        # Check for color options
                        color_buttons = await product.locator('button.qMneo img').all()
                        if color_buttons:
                            colors = []
                            for btn in color_buttons:
                                alt_text = await btn.get_attribute("alt")
                                if alt_text and alt_text != "null":
                                    colors.append(alt_text)
                            if colors:
                                additional_info.append(f"Colors: {', '.join(colors)}")
                    except:
                        pass

                    try:
                        # Check for coupon code
                        coupon_loc = product.locator('input.fpacCoupon')
                        if await coupon_loc.count() > 0:
                            coupon_code = await coupon_loc.get_attribute("value")
                            if coupon_code:
                                additional_info.append(f"Coupon: {coupon_code}")
                    except:
                        pass

                    # Combine all additional info
                    additional_info_str = " | ".join(filter(None, additional_info)) if additional_info else ""

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                    records.append((unique_id, current_date, page_title, product_name, None, material, price, size_weight, additional_info_str))
                    sheet.append([
                        current_date, 
                        page_title, 
                        product_name, 
                        None, 
                        material, 
                        price, 
                        size_weight, 
                        time_only, 
                        image_url,
                        additional_info_str
                    ])

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
                                records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7], record[8])
                                break
                    except asyncio.TimeoutError:
                        logging.warning(f"Timeout downloading image for row {row_num}")

                all_records.extend(records)
                success_count += 1

                # Save progress after each page
                wb.save(file_path)
                logging.info(f"Progress saved after page {page_count}")

                page_count += 1
                await asyncio.sleep(random.uniform(2, 5))

            except Exception as e:
                logging.error(f"Error processing page {page_count}: {str(e)}")
                if page:
                    await page.close()
                if browser:
                    await browser.close()
                wb.save(file_path)
                continue

    # Final save and database operations
    if not all_records:
        return None, None, None
    
    wb.save(file_path)
    log_event(f"Data saved to {file_path}")

    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path