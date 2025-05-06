import asyncio
import re
import os
import uuid
import logging
import base64
import random
import time
from datetime import datetime
from io import BytesIO
import mimetypes
import httpx
from PIL import Image as PILImage
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from flask import Flask
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError, Error
from PIL import Image
from utils import get_public_ip, log_event, sanitize_filename
from database import insert_into_db
from limit_checker import update_product_count
from proxysetup import get_browser_with_proxy_strategy
# Load environment


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXCEL_DATA_PATH = os.path.join(BASE_DIR, 'static', 'ExcelData')
IMAGE_SAVE_PATH = os.path.join(BASE_DIR, 'static', 'Images')

# Resize image if needed
def resize_image(image_data, max_size=(100, 100)):
    try:
        img = PILImage.open(BytesIO(image_data))
        img.thumbnail(max_size, PILImage.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception as e:
        log_event(f"Error resizing image: {e}")
        return image_data

# Transform URL to get high-res image

def modify_image_url(image_url):
    """Enhance Macy's image URL to get higher resolution version"""
    if not image_url or image_url == "N/A":
        return image_url

    # Replace dimensions in query parameters
    modified_url = re.sub(r'wid=\d+', 'wid=1200', image_url)
    modified_url = re.sub(r'hei=\d+', 'hei=1200', modified_url)
    
    # Replace image quality parameters
    modified_url = re.sub(r'qlt=[^&]+', 'qlt=95', modified_url)
    
    return modified_url


# Async image downloader
mimetypes.add_type('image/webp', '.webp')
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

# Human-like delay
def random_delay(min_sec=1, max_sec=3):
    time.sleep(random.uniform(min_sec, max_sec))




def build_macys_pagination_url(base_url: str, page_index: int) -> str:
    if page_index == 0:
        return base_url
    else:
        if base_url.endswith('/'):
            base_url = base_url.rstrip('/')
        parts = base_url.split('?')
        path = parts[0]
        query = f"?{parts[1]}" if len(parts) > 1 else ""
        return f"{path}/Pageindex/{page_index}{query}"
    
    
async def handle_macys(url, max_pages):
    ip_address = get_public_ip()
    logging.info(f"Scraping started for: {url} from IP: {ip_address}, max_pages: {max_pages}")

    os.makedirs(EXCEL_DATA_PATH, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_folder = os.path.join(IMAGE_SAVE_PATH, timestamp)
    os.makedirs(image_folder, exist_ok=True)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Products"
    headers = ["Current Date", "Header", "Product Name", "Image", "Material", "Price", 
               "Size/Weight", "Time", "ImagePath", "Additional Info"]
    sheet.append(headers)

    all_records = []
    filename = f"Macy's_{datetime.now().strftime('%Y-%m-%d_%H.%M')}.xlsx"
    file_path = os.path.join(EXCEL_DATA_PATH, filename)

    page_count = 1
    success_count = 0

    async with async_playwright() as p:
        while page_count <= max_pages:
            current_url = build_macys_pagination_url(url, page_count)
            browser = None
            page = None
            try:
                product_wrapper = ".product-thumbnail-container"
                browser, page = await get_browser_with_proxy_strategy(p, current_url, product_wrapper)
                log_event(f"Successfully loaded: {current_url}")

                # Scroll to load all items
                prev_count = 0
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(random.uniform(1, 2))
                    count = await page.locator('.v-carousel-content').count()
                    if count == prev_count:
                        break
                    prev_count = count

                page_title = await page.title()
                current_date = datetime.now().strftime("%Y-%m-%d")
                time_only = datetime.now().strftime("%H.%M")

                product_container = page.locator("ul.grid-x.small-up-2").first
                products = await product_container.locator("li.cell.sortablegrid-product").all()
                
                logging.info(f"Total products scraped: {len(products)}")
                records = []
                image_tasks = []

                for row_num, product in enumerate(products, start=len(sheet["A"]) + 1):
                    additional_info = []
                    
                    try:
                        # Product Name
                        product_name_tag = product.locator("div.product-name.medium")
                        product_name = await product_name_tag.text_content() if await product_name_tag.count() > 0 else "N/A"
                        product_name = product_name.strip() if product_name else "N/A"
                    except:
                        product_name = "N/A"

                    # Price handling - capture both current and original price
                    price_info = []
                    try:
                        # Current price
                        discount_tag = product.locator("span.discount.is-tier2")
                        if await discount_tag.count() > 0:
                            discounted_text = await discount_tag.text_content()
                            current_price = discounted_text.strip().split("(")[0].strip()
                            price_info.append(current_price)
                            
                            # Original price
                            original_price_tag = product.locator("span.price-strike-sm")
                            if await original_price_tag.count() > 0:
                                original_price = (await original_price_tag.text_content()).strip()
                                if original_price and original_price != current_price:
                                    price_info.append(original_price)
                                    
                                    # Discount percentage
                                    discount_pct_tag = product.locator("span.sale-percent.percent-small")
                                    if await discount_pct_tag.count() > 0:
                                        discount_pct = await discount_pct_tag.text_content()
                                        additional_info.append(f"Discount: {discount_pct.strip()}")
                                    else:
                                        try:
                                            current_num = float(current_price.replace('INR', '').replace(',', '').strip())
                                            original_num = float(original_price.replace('INR', '').replace(',', '').strip())
                                            discount_pct = round((1 - (current_num / original_num)) * 100)
                                            additional_info.append(f"Discount: {discount_pct}%")
                                        except:
                                            pass
                        else:
                            # Regular price
                            regular_price_tag = product.locator("span.price-reg.is-tier1")
                            if await regular_price_tag.count() > 0:
                                price_info.append((await regular_price_tag.text_content()).strip())
                    except Exception as e:
                        logging.warning(f"Error extracting price: {e}")
                        price_info = ["N/A"]
                    
                    price = " | ".join(price_info) if price_info else "N/A"

                    # Image extraction with fallbacks
                    try:
                        image_tag = product.locator('img[ref_key="imageRef"]').first
                        if await image_tag.count() > 0:
                            image_url = await image_tag.get_attribute("data-src") or await image_tag.get_attribute("src") or "N/A"
                            if image_url and image_url.startswith("//"):
                                image_url = f"https:{image_url}"
                        else:
                            image_url = "N/A"
                    except:
                        image_url = "N/A"

                    if product_name == "N/A" or price == "N/A" or image_url == "N/A":
                        print(f"Skipping product due to missing data: Name: {product_name}, Price: {price}, Image: {image_url}")
                        continue


                    # Material Type (more general than just gold)
                    material_pattern = r"\b\d{1,2}K\s*(?:White|Yellow|Rose)?\s*Gold\b|\bPlatinum\b|\bSterling Silver\b|\bTitanium\b"
                    material_match = re.search(material_pattern, product_name, re.IGNORECASE)
                    material = material_match.group() if material_match else "N/A"

                    # Size/Weight (more generic than just diamond weight)
                    size_weight_pattern = r"(\d+(?:\.\d+)?\s*(?:ct|ctw|carat|inch|cm|mm)\b)"
                    size_weight_match = re.search(size_weight_pattern, product_name, re.IGNORECASE)
                    size_weight = size_weight_match.group() if size_weight_match else "N/A"

                    # Additional product info
                    try:
                        # Check for promotions/badges
                        promo_badge = product.locator("div.corner-badge")
                        if await promo_badge.count() > 0:
                            promo_text = await promo_badge.text_content()
                            additional_info.append(f"Promo: {promo_text.strip()}")
                        
                        bonus_offer = product.locator("div.badge-wrapper span")
                        if await bonus_offer.count() > 0:
                            offer_text = await bonus_offer.text_content()
                            if offer_text.strip():
                                additional_info.append(f"Bonus: {offer_text.strip()}")
                    except:
                        pass

                    try:
                        # Check for ratings
                        rating_container = product.locator("span.review-star-wrapper")
                        if await rating_container.count() > 0:
                            rating_aria = await rating_container.get_attribute("aria-label")
                            if rating_aria:
                                additional_info.append(f"Rating: {rating_aria.replace('Rated ', '').replace(' stars', '')}")
                            
                            review_count = product.locator("span.rating-description span")
                            if await review_count.count() > 0:
                                count_text = await review_count.text_content()
                                if count_text.isdigit():
                                    additional_info.append(f"Reviews: {count_text}")
                    except:
                        pass

                    try:
                        # Check for product brand
                        brand_tag = product.locator("div.product-brand.medium")
                        if await brand_tag.count() > 0:
                            brand_text = await brand_tag.text_content()
                            if brand_text.strip():
                                additional_info.append(f"Brand: {brand_text.strip()}")
                    except:
                        pass

                    # Combine all additional info with pipe delimiter
                    additional_info_str = " | ".join(additional_info) if additional_info else ""

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
                            except Exception as e:
                                logging.error(f"Error embedding image: {e}")
                                image_path = "N/A"
                        
                        for i, record in enumerate(records):
                            if record[0] == unique_id:
                                records[i] = (record[0], record[1], record[2], record[3], image_path, record[5], record[6], record[7], record[8])
                                break
                    except asyncio.TimeoutError:
                        logging.warning(f"Image download timed out for row {row_num}")

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
                
                # Add delay between pages
                await asyncio.sleep(random.uniform(2, 5))
            
            page_count += 1

    if not all_records:
        return None, None, None
    # Final save and database operations
    wb.save(file_path)
    log_event(f"Data saved to {file_path}")

    with open(file_path, "rb") as file:
        base64_encoded = base64.b64encode(file.read()).decode("utf-8")

    insert_into_db(all_records)
    update_product_count(len(all_records))

    return base64_encoded, filename, file_path


