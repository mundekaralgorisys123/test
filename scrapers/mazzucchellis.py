import asyncio
import re
import os
import uuid
import time
import random
import logging
import base64
from datetime import datetime
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from dotenv import load_dotenv
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
import httpx
from playwright.async_api import async_playwright, TimeoutError,Error
import traceback
from typing import List, Tuple
# Load .env variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")
PROXY_SERVER = os.getenv("PROXY_SERVER")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

def modify_image_url(image_url):
    if not image_url or image_url == "N/A":
        return image_url
    query_params = ""
    if "?" in image_url:
        image_url, query_params = image_url.split("?", 1)
        query_params = f"?{query_params}"
    modified_url = re.sub(r'(_260)(?=\.\w+$)', '_1200', image_url)
    return modified_url + query_params

async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.jpg"
    image_full_path = os.path.join(image_folder, image_filename)
    modified_url = modify_image_url(image_url)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(retries):
            try:
                response = await client.get(modified_url)
                response.raise_for_status()
                with open(image_full_path, "wb") as f:
                    f.write(response.content)
                return image_full_path
            except httpx.RequestError as e:
                logging.warning(f"Retry {attempt + 1}/{retries} - Error downloading {product_name}: {e}")
    logging.error(f"Failed to download {product_name} after {retries} attempts.")
    return "N/A"

def random_delay(min_sec=1, max_sec=3):
    """Introduce a random delay to mimic human-like behavior."""
    time.sleep(random.uniform(min_sec, max_sec))

########################################  safe_goto_and_wait ####################################################################


async def safe_goto_and_wait(page, url,isbri_data, retries=2):
    for attempt in range(retries):
        try:
            print(f"[Attempt {attempt + 1}] Navigating to: {url}")
            
            if isbri_data:
                await page.goto(url, timeout=180_000, wait_until="domcontentloaded")
            else:
                await page.goto(url, wait_until="networkidle", timeout=180_000)

            # Wait for the selector with a longer timeout
            product_cards = await page.wait_for_selector(".nn-collection-main", state="attached", timeout=30000)

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


########################################  get browser with proxy ####################################################################
      

async def get_browser_with_proxy_strategy(p, url: str):
    """
    Dynamically checks robots.txt and selects proxy accordingly
    Always uses proxies - never scrapes directly
    """
    parsed_url = httpx.URL(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.host}"
    
    # 1. Fetch and parse robots.txt
    disallowed_patterns = await get_robots_txt_rules(base_url)
    
    # 2. Check if URL matches any disallowed pattern
    is_disallowed = check_url_against_rules(str(parsed_url), disallowed_patterns)
    
    # 3. Try proxies in order (bri-data first if allowed, oxylabs if disallowed)
    proxies_to_try = [
        PROXY_URL if not is_disallowed else {
            "server": PROXY_SERVER,
            "username": PROXY_USERNAME,
            "password": PROXY_PASSWORD
        },
        {  # Fallback to the other proxy
            "server": PROXY_SERVER,
            "username": PROXY_USERNAME,
            "password": PROXY_PASSWORD
        } if not is_disallowed else PROXY_URL
    ]

    last_error = None
    for proxy_config in proxies_to_try:
        browser = None
        try:
            isbri_data = False
            if proxy_config == PROXY_URL:
                logging.info("Attempting with bri-data proxy (allowed by robots.txt)")
                browser = await p.chromium.connect_over_cdp(PROXY_URL)
                isbri_data = True
            else:
                logging.info("Attempting with oxylabs proxy (required by robots.txt)")
                browser = await p.chromium.launch(
                    proxy=proxy_config,
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-web-security'
                    ]
                )

            context = await browser.new_context()
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """)
            page = await context.new_page()
            
            await safe_goto_and_wait(page, url,isbri_data)
            return browser, page

        except Exception as e:
            last_error = e
            error_trace = traceback.format_exc()
            logging.error(f"Proxy attempt failed:\n{error_trace}")
            if browser:
                await browser.close()
            continue

    error_msg = (f"Failed to load {url} using all proxy options. "
                f"Last error: {str(last_error)}\n"
                f"URL may be disallowed by robots.txt or proxies failed.")
    logging.error(error_msg)
    raise RuntimeError(error_msg)




async def get_robots_txt_rules(base_url: str) -> List[str]:
    """Dynamically fetch and parse robots.txt rules"""
    robots_url = f"{base_url}/robots.txt"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(robots_url, timeout=10)
            if resp.status_code == 200:
                return [
                    line.split(":", 1)[1].strip()
                    for line in resp.text.splitlines()
                    if line.lower().startswith("disallow:")
                ]
    except Exception as e:
        logging.warning(f"Couldn't fetch robots.txt: {e}")
    return []


def check_url_against_rules(url: str, disallowed_patterns: List[str]) -> bool:
    """Check if URL matches any robots.txt disallowed pattern"""
    for pattern in disallowed_patterns:
        try:
            # Handle wildcard patterns
            if "*" in pattern:
                regex_pattern = pattern.replace("*", ".*")
                if re.search(regex_pattern, url):
                    return True
            # Handle path patterns
            elif url.startswith(f"{pattern}"):
                return True
            # Handle query parameters
            elif ("?" in url) and any(
                f"{param}=" in url 
                for param in pattern.split("=")[0].split("*")[-1:]
                if "=" in pattern
            ):
                return True
        except Exception as e:
            logging.warning(f"Error checking pattern {pattern}: {e}")
    return False



async def handle_mazzucchellis(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Starting scrape for {url} from IP: {ip_address}")

    if not os.path.exists(EXCEL_DATA_PATH):
        os.makedirs(EXCEL_DATA_PATH)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)
    all_records = []
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Kt", "Price", "Total Dia wt", "Time", "ImagePath", "Additional Info"]
    sheet.append(headers)

    current_date = datetime.now().strftime("%Y-%m-%d")
    time_only = datetime.now().strftime("%H.%M")
    filename = f'handle_mazzucchellis_{datetime.now().strftime("%Y-%m-%d_%H.%M")}.xlsx'
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    records = []
    image_tasks = []

    async with httpx.AsyncClient() as session:
        load_more_clicks = 1
        previous_count = 0
        browser = None
        page = None

        while load_more_clicks <= max_pages:
            async with async_playwright() as p:
                # Create a new browser instance for each page
                browser, page = await get_browser_with_proxy_strategy(p, url)
                log_event(f"Successfully loaded: {url}")

            
                for _ in range(load_more_clicks):
                    for attempt in range(2):  # Try max 2 times per click
                        try:
                            # Scope to the parent pagination container
                            pagination_container = page.locator("div.nn-pagination.text-center")
                            load_more_button = pagination_container.locator("button.btn.btn-outline-primary.btn-filter-load-more.btn-loader")

                            await load_more_button.wait_for(state="visible", timeout=5000)
                            await load_more_button.scroll_into_view_if_needed()

                            if await load_more_button.is_enabled():
                                await load_more_button.click()
                                logging.info(f"Clicked 'Load More' button on attempt {attempt + 1}.")
                                await asyncio.sleep(2)
                                break  # Exit retry loop on success
                            else:
                                logging.info("Load More button is visible but not enabled.")
                                break

                        except Exception as e:
                            logging.warning(f"Attempt {attempt + 1} failed to click 'Load More': {e}")
                            await asyncio.sleep(2)  # Retry delay

                    else:
                        logging.warning("Failed to click 'Load More' after 2 attempts. Stopping.")
                        break
                
                
               


                all_products = await page.query_selector_all(".nn-product-preview")
                total_products = len(all_products)
                new_products = all_products[previous_count:]
                logging.info(f"Page {load_more_clicks}: Total = {total_products}, New = {len(new_products)}")
                previous_count += len(new_products)
                
                print(f"Page {load_more_clicks}: Scraping {len(new_products)} new products.")
                page_title = await page.title()

                # for idx, product in enumerate(new_products):
                for row_num, product in enumerate(new_products, start=len(sheet["A"]) + 1):
                    try:
                        product_name_tag = await product.query_selector('h3 a')
                        product_name = await product_name_tag.inner_text() if product_name_tag else "N/A"
                    except:
                        product_name = "N/A"

                    try:
                        price_tag = await product.query_selector('.nn-price-current')
                        price = await price_tag.inner_text() if price_tag else "N/A"
                    except:
                        price = "N/A"

                    try:
                        image_tag = await product.query_selector("div.nn-product-preview-image img")
                        image_url = await image_tag.get_attribute("src") if image_tag else "N/A"

                        # Normalize protocol-relative URLs
                        if image_url and image_url.startswith("//"):
                            image_url = "https:" + image_url

                    except Exception as e:
                        print(f"Error getting image URL: {e}")
                        image_url = "N/A"
                        
                    additional_info = []

                    try:
                        tag_els = await product.query_selector_all("div.nn-product-preview-tag-list span.nn-product-preview-tag-item")
                        
                        if tag_els:
                            for tag_el in tag_els:
                                tag_text = await tag_el.inner_text()
                                if tag_text:
                                    additional_info.append(tag_text.strip())
                        else:
                            additional_info.append("N/A")

                    except Exception as e:
                        additional_info.append("N/A")

                    # Combine all tag information into a single string
                    additional_info_str = " | ".join(additional_info)
                        
                        
                    if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                        print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                        continue        




                    kt_match = re.search(
                        r"\b\d{1,2}ct\s*(White|Yellow|Rose)?\s*Gold\b|\bPlatinum\b|\bSilver\b|\bRhodium Plated\b",
                        product_name,
                        re.IGNORECASE
                    )
                    kt = kt_match.group() if kt_match else "Not found"


                    diamond_match = re.search(r"\b(\d+(\.\d+)?)\s*(?:ct|ctw|carat)\b", product_name, re.IGNORECASE)
                    diamond_weight = f"{diamond_match.group(1)} ct" if diamond_match else "N/A"

                    unique_id = str(uuid.uuid4())
                    image_tasks.append((row_num, unique_id, asyncio.create_task(
                        download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
                    )))

                    records.append((unique_id, current_date, page_title, product_name, None, kt, price, diamond_weight,additional_info_str))
                    sheet.append([current_date, page_title, product_name, None, kt, price, diamond_weight, time_only, image_url,additional_info_str])

                # Process image downloads and attach them to Excel
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

                await browser.close()
            load_more_clicks += 1

        # Save Excel
        
        if not records:
            return None, None, None
        wb.save(file_path)
        log_event(f"Data saved to {file_path} | IP: {ip_address}")
        
        # Encode the file in base64
        with open(file_path, "rb") as file:
            base64_encoded = base64.b64encode(file.read()).decode("utf-8")

        insert_into_db(records)
        update_product_count(len(records))
        return base64_encoded, filename, file_path
