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
    """Modify the image URL to replace '_260' with '_1200' while keeping query parameters."""
    if not image_url or image_url == "N/A":
        return image_url

    # Extract and preserve query parameters
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"

    # Replace '_260' with '_1200' while keeping the rest of the URL intact
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)

    return modified_url + query_params  # Append query parameters if they exist


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
            

async def safe_wait_for_selector(page, selector, timeout=15000, retries=3):
    """Retry waiting for a selector."""
    for attempt in range(retries):
        try:
            return await page.wait_for_selector(selector, state="attached", timeout=timeout)
        except TimeoutError:
            logging.warning(f"TimeoutError on attempt {attempt + 1}/{retries} waiting for {selector}")
            if attempt < retries - 1:
                random_delay(1, 2)  # Add delay before retrying
            else:
                raise

async def handle_monicavinader(url, max_pages):
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
    headers = ["Current Date", "Header", "Product Name", "Image", "Material", "Price", "Gemstone Info", 
               "Time", "ImagePath", "Additional Info"]
    sheet.append(headers)

    all_records = []
    filename = f"handle_monicavinader_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    while page_count <= max_pages:
        current_url = f"{url}?page={page_count}" if page_count > 1 else url
        logging.info(f"Processing page {page_count}: {current_url}")
        
        browser = None
        page = None
        try:
            async with async_playwright() as p:
                browser, page = await get_browser_with_proxy_strategy(p, current_url, ".product-catalogue")
                log_event(f"Successfully loaded: {current_url}")

                # Scroll to load all products
                prev_product_count = 0
                for _ in range(10):
                    await page.eval_on_selector(
                        ".product-catalogue-wrap",
                        "(el) => el.scrollTo(0, el.scrollHeight)"
                    )
                    await asyncio.sleep(random.uniform(1, 2))
                    current_product_count = await page.locator('article.product-preview').count()
                    if current_product_count == prev_product_count:
                        break
                    prev_product_count = current_product_count

                product_wrapper = await page.query_selector("div.product-catalogue-wrap") 
                products = await product_wrapper.query_selector_all("article.product-preview") if product_wrapper else []
                logging.info(f"Total products found on page {page_count}: {len(products)}")

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    additional_info = []
                    
                    # Extract product name and combine with description
                    product_name = "N/A"
                    material = "N/A"
                    try:
                        name_tag = await product.query_selector("h3.product-preview__title")
                        desc_tag = await product.query_selector("p.product-preview__description")
                        
                        name_text = (await name_tag.inner_text()).strip() if name_tag else ""
                        desc_text = (await desc_tag.inner_text()).strip() if desc_tag else ""
                        
                        if name_text and desc_text:
                            product_name = f"{name_text}, {desc_text}"
                        elif name_text:
                            product_name = name_text
                        else:
                            product_name = "N/A"
                            
                        material = desc_text if desc_text else "N/A"
                    except Exception:
                        product_name = "N/A"
                        material = "N/A"

                    # Extract price information
                    price = "N/A"
                    original_price = "N/A"
                    try:
                        price_tag = await product.query_selector("p.product-preview__price")
                        if price_tag:
                            price = (await price_tag.inner_text()).strip()
                            # Check for sale price if available (not visible in sample HTML)
                            price_text = f"Price: {price}"
                    except Exception:
                        price_text = "N/A"

                    # Extract product URL
                    product_url = "N/A"
                    try:
                        product_link = await product.query_selector("a.product-preview__link")
                        if product_link:
                            product_url = await product_link.get_attribute("href")
                            if product_url and product_url != "N/A":
                                if not product_url.startswith('http'):
                                    product_url = f"https://www.monicavinader.com{product_url}"
                                additional_info.append(f"URL: {product_url}")
                    except Exception:
                        pass

                    # Extract data attributes for additional info
                    try:
                        if product_link:
                            data_attrs = {
                                'Product ID': await product_link.get_attribute("data-gaid"),
                                'Variation ID': await product_link.get_attribute("data-cnstrc-item-variation-id"),
                                'Collection': await product_link.get_attribute("data-cnstrc-item-section")
                            }
                            
                            for key, value in data_attrs.items():
                                if value and value != "N/A":
                                    additional_info.append(f"{key}: {value}")
                    except Exception:
                        pass

                    # Extract color/material options from swatches
                    try:
                        swatches = await product.query_selector_all("button.swatch")
                        if swatches:
                            colors = []
                            for swatch in swatches:
                                color_label = await swatch.get_attribute("aria-label")
                                if color_label and color_label != "N/A":
                                    colors.append(color_label)
                            if colors:
                                additional_info.append(f"Available In: {'|'.join(colors)}")
                    except Exception:
                        pass

                    # Check for badges (New In, Best Seller, etc.)
                    try:
                        badge = await product.query_selector("div.flash-badge--listing span")
                        if badge:
                            badge_text = (await badge.inner_text()).strip()
                            if badge_text and badge_text != "N/A":
                                additional_info.append(f"Status: {badge_text}")
                    except Exception:
                        pass

                    # Extract image URLs (primary and hover)
                    image_url = "N/A"
                    try:
                        # Primary image
                        primary_img = await product.query_selector("figure.product-preview__image--no-blend img.product-listing__image")
                        if primary_img:
                            image_url = await primary_img.get_attribute("src")
                            if image_url and image_url != "N/A":
                                if not image_url.startswith('http'):
                                    image_url = f"https:{image_url}" if image_url.startswith('//') else f"https://www.monicavinader.com{image_url}"
                    except Exception:
                        image_url = "N/A"

                    if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                        print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                        continue
                    
                    # Extract gemstone information from product name
                    gemstone_info = "N/A"
                    try:
                        gemstone_pattern = r"\b(Diamond|Ruby|Sapphire|Emerald|Aquamarine|Pearl|Onyx|Topaz|Opal|Amethyst|Citrine|Garnet|Peridot)\b"
                        gemstone_match = re.search(gemstone_pattern, product_name, re.IGNORECASE)
                        gemstone_info = gemstone_match.group() if gemstone_match else "N/A"
                    except Exception:
                        gemstone_info = "N/A"

                    # Combine all additional info with | separator
                    additional_info_text = " | ".join(additional_info) if additional_info else ""

                    unique_id = str(uuid.uuid4())
                    if image_url and image_url != "N/A":
                        image_tasks.append((row_num, unique_id, asyncio.create_task(
                            download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                        )))

                    records.append((unique_id, current_date, page_title, product_name, None, material, 
                                  price_text, gemstone_info, additional_info_text))
                    sheet.append([current_date, page_title, product_name, None, material, price_text, 
                                gemstone_info, time_only, image_url, additional_info_text])

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
                                records[i] = (record[0], record[1], record[2], record[3], image_path, 
                                             record[5], record[6], record[7], record[8])
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
            wb.save(file_path)
        finally:
            if page:
                await page.close()
            if browser:
                await browser.close()
            
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