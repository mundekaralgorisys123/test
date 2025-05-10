import asyncio
import random
import re
import os
import uuid
import logging
import base64
from datetime import datetime
import aiofiles
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from flask import Flask
from dotenv import load_dotenv
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
import httpx
from playwright.async_api import async_playwright, TimeoutError
from proxysetup import get_browser_with_proxy_strategy

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')


async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    image_name = f"{product_name}_{timestamp}_{unique_id}.jpg"
    image_path = os.path.join(image_folder, image_name)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.vancleefarpels.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=20, headers=headers) as client:
                response = await client.get(image_url)
                response.raise_for_status()
                with open(image_path, 'wb') as f:
                    f.write(response.content)
                print(f"[Downloaded] {image_path}")
                return image_path

        except httpx.ReadTimeout:
            print(f"[Timeout] Attempt {attempt + 1}/{retries} - {image_url}")
            await asyncio.sleep(1.5 * (attempt + 1))

        except httpx.HTTPStatusError as e:
            print(f"[HTTP Error] {e.response.status_code} - {image_url}")
            return None

        except Exception as e:
            print(f"[Error] {e} while downloading {image_url}")
            return None

    print(f"[Failed] {image_url}")
    return None

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
                product_wrapper = "ul.results-list.grid-mode"
                browser, page = await get_browser_with_proxy_strategy(p, url,product_wrapper)
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

                    if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                        print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                        continue 
                    
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
        if not records:
            return None, None, None

        # Save the workbook
        wb.save(file_path)
        log_event(f"Data saved to {file_path}")

        # Encode the file in base64
        with open(file_path, "rb") as file:
            base64_encoded = base64.b64encode(file.read()).decode("utf-8")

        # Insert data into the database and update product count
        insert_into_db(records)
        update_product_count(len(records))

        # Return necessary information
        return base64_encoded, filename, file_path

