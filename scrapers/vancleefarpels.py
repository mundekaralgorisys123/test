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

    # Use httpx.AsyncClient for asynchronous requests
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(retries):
            try:
                response = await client.get(image_url)
                response.raise_for_status()  # Raises an exception for 4xx/5xx status codes
                
                # Successfully downloaded image
                with open(image_full_path, "wb") as f:
                    f.write(response.content)

                logging.info(f"Successfully downloaded {product_name} to {image_full_path}")
                return image_full_path

            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name} from {image_url}: {e}")

                # Optionally, log the response details if available
                if e.response:
                    logging.debug(f"Response status: {e.response.status_code}, content: {e.response.text}")
                
                # If it's the last attempt, log the failure
                if attempt == retries - 1:
                    logging.error(f"Failed to download {product_name} after {retries} attempts from {image_url}")

    return "N/A"

async def handle_vancleefarpels(url, max_pages):
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

    async with httpx.AsyncClient() as session:
        load_more_clicks = 1
        previous_count = 0

        while load_more_clicks <= max_pages:
            async with async_playwright() as p:
                # Create a new browser instance for each page
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                page = await browser.new_page()

                try:
                    await page.goto(url, timeout=120000)
                except Exception as e:
                    logging.warning(f"Failed to load URL {url}: {e}")
                    await browser.close()
                    continue  # move to the next iteration

                # Simulate clicking 'Load More' number of times
                for _ in range(load_more_clicks - 1):
                    try:
                        # Locate the 'Load More' button using the correct selector
                        load_more_button = page.locator("button#loadMore.action-button.load-more.vca-underline")
                        
                        # Check if the button is visible and click it
                        if await load_more_button.is_visible():
                            await load_more_button.click()
                            await asyncio.sleep(2)  # Delay to allow new products to load
                    except Exception as e:
                        logging.warning(f"Could not click 'Load More': {e}")
                        break


                all_products = await page.query_selector_all("li.vca-srl-product-tile")

                total_products = len(all_products)
                new_products = all_products[previous_count:]
                logging.info(f"Page {load_more_clicks}: Total = {total_products}, New = {len(new_products)}")
                previous_count = total_products

                print(f"Page {load_more_clicks}: Scraping {len(new_products)} new products.")
                page_title = await page.title()

                for row_num, product in enumerate(new_products, start=len(sheet["A"]) + 1):
                    try:
                        # Extract product name from the <h2> tag
                        product_name_tag = await product.query_selector('h2.product-name.vca-product-list-01')
                        product_name = await product_name_tag.inner_text() if product_name_tag else "N/A"
                    except Exception as e:
                        logging.error(f"Error fetching product name: {e}")
                        product_name = "N/A"

                    try:
                        # Extract price from the <span> tag with class 'vca-price'
                        price_tag = await product.query_selector('span.vca-price')
                        price = await price_tag.inner_text() if price_tag else "N/A"
                    except Exception as e:
                        logging.error(f"Error fetching price: {e}")
                        price = "N/A"


                    try:
                        # Initialize image_url as 'N/A'
                        image_url = "N/A"
                        
                        # First try to get the active slide
                        active_slide = await product.query_selector('div.swiper-slide-active')
                        
                        if active_slide:
                            # Locate the image element within the active slide
                            image_element = await active_slide.query_selector("img")
                            if image_element:
                                # Get the 'src' attribute for the image
                                image_src = await image_element.get_attribute("src")
                                
                                if image_src:
                                    # Handle relative URLs and final URL construction
                                    if not image_src.startswith(("http", "//")):
                                        image_url = f"https://www.vancleefarpels.com{image_src}"
                                    else:
                                        image_url = image_src
                        else:
                            image_url = "N/A"  # If no active slide found, default to "N/A"
                            
                    except Exception as e:
                        logging.error(f"Image extraction error: {e}")
                        image_url = "N/A"  # Default to "N/A" in case of error

                    # image_url should now contain the extracted URL or "N/A" if an error occurs


                    print(image_url)
                    kt_match = re.search(r"\b\d{1,2}K\s*(?:White|Yellow|Rose)?\s*Gold\b|\bPlatinum\b|\bSilver\b", product_name, re.IGNORECASE)
                    kt = kt_match.group() if kt_match else "Not found"

                    diamond_match = re.search(r"\b(\d+(\.\d+)?)\s*(?:ct|ctw|carat)\b", product_name, re.IGNORECASE)
                    diamond_weight = f"{diamond_match.group(1)} ct" if diamond_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

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
            load_more_clicks += 1

        # Save Excel
        filename = f'handle_vancleefarpels_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
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
