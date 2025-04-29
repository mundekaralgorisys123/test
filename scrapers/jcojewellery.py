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
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
# Load .env variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")

app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(app.root_path, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

def get_high_res_image_url(image_url: str, desired_width: int = 2000) -> str:
    parsed = urlparse(image_url)
    query = parse_qs(parsed.query)

    # Update or insert the width
    query['width'] = [str(desired_width)]
    if 'height' in query:
        query.pop('height')  # Remove height to avoid distortion

    # Reconstruct the URL with new query params
    new_query = urlencode(query, doseq=True)
    new_url = urlunparse(parsed._replace(query=new_query))
    return new_url


async def download_image(session, image_url, product_name, timestamp, image_folder, unique_id):
    if not image_url or image_url == "N/A":
        return "N/A"
    
    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    
    # Modify the URL to fetch high-resolution image if available
    modified_url = get_high_res_image_url(image_url)
    
    for attempt in range(3):
        try:
            # Download the image
            resp = await session.get(modified_url, timeout=10)
            resp.raise_for_status()
            with open(image_full_path, "wb") as f:
                f.write(resp.content)
            return image_full_path
        except Exception as e:
            logging.warning(f"Retry {attempt + 1}/3 - Error downloading {product_name}: {e}")
    
    logging.error(f"Failed to download {product_name} after 3 attempts.")
    return "N/A"


async def handle_jcojewellery(url, max_pages):
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
                        # More precise selection of the "Load More" button
                        load_more_button = page.get_by_role("button", name="Load More")

                        if await load_more_button.is_visible():
                            await load_more_button.click()
                            await page.wait_for_timeout(2000)
                        else:
                            break
                    except Exception as e:
                        logging.warning(f"Could not click 'Load More': {e}")
                        break


                                            


               # Wait for the product wrapper that contains all products
                product_wrapper = await page.wait_for_selector("div.products-outer-wrapper", timeout=30000)

                # Select all product cards inside the wrapper
                all_products = await product_wrapper.query_selector_all("div.product-card-wrapper")

                

                total_products = len(all_products)
                new_products = all_products[previous_count:]
                logging.info(f"Page {load_more_clicks}: Total = {total_products}, New = {len(new_products)}")
                previous_count = total_products

                print(f"Page {load_more_clicks}: Scraping {len(new_products)} new products.")
                page_title = await page.title()

                for idx, product in enumerate(new_products):
                    try:
                        product_name = await (await product.query_selector("a.p4.width-100.regular-400.color-url")).inner_text()
                    except:
                        product_name = "N/A"


                    try:
                        # Price is in a <span> with class "money"
                        price = await (await product.query_selector("span.p5")).inner_text()
                    except:
                        price = "N/A"
                        
                
                    try:
                        # Use a broader query selector to find the image
                        image_element = await product.query_selector("img")

                        image_url = None
                        if image_element:
                            # Try srcset/data-srcset first for high-res images
                            image_url = await image_element.get_attribute("data-srcset")
                            if not image_url:
                                image_url = await image_element.get_attribute("srcset")

                            # Fallback to data-src/src
                            if not image_url:
                                image_url = await image_element.get_attribute("data-src")
                            if not image_url:
                                image_url = await image_element.get_attribute("src")

                            # Parse srcset to get the highest resolution image
                            if image_url and " " in image_url:
                                image_url = image_url.split(",")[-1].split()[0]

                            # Ensure URL has https prefix
                            if image_url and image_url.startswith("//"):
                                image_url = "https:" + image_url

                        image_url = image_url if image_url else "N/A"

                    except Exception as e:
                        print(f"Error extracting product image URL: {e}")
                        image_url = "N/A"



                        
                    gold_type_match = re.search(r"\b\d{1,2}K(?:\s+\w+){0,3}\s+Gold\b", product_name, re.IGNORECASE)
                    kt = gold_type_match.group() if gold_type_match else "Not found"

                    diamond_weight_match = re.search(r"(\d+(\.\d+)?)\s*(ct|carat)", product_name, re.IGNORECASE)
                    diamond_weight = f"{diamond_weight_match.group(1)} ct" if diamond_weight_match else "N/A"
                    
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
            load_more_clicks += 1

        # Save Excel
        filename = f'handle_jcojewellery_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
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
