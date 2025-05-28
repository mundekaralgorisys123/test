# import os
# import pymssql
# from dotenv import load_dotenv
# from utils import log_event  # Make sure this import is correct

# # Load environment variables
# # load_dotenv()

# # Database Configuration
# # DB_CONFIG = {
# #     "server": os.getenv("DB_SERVER", "192.168.0.102"),
# #     "user": os.getenv("DB_USER", "sa"),
# #     "password": os.getenv("DB_PASSWORD", "admin@123"),
# #     "database": os.getenv("DB_NAME", "Webstudy"),
# # }

# # Database Configuration
# DB_CONFIG = {
#     "server": os.getenv("DB_SERVER"),
#     "user": os.getenv("DB_USER"),
#     "password": os.getenv("DB_PASSWORD"),
#     "database": os.getenv("DB_NAME"),
# }


# def get_db_connection():
#     try:
#         conn = pymssql.connect(
#             server=DB_CONFIG['server'],
#             user=DB_CONFIG['user'],
#             password=DB_CONFIG['password'],
#             database=DB_CONFIG['database'],
#             port=1433
#         )
#         print("Database connection successful!")
#         return conn
#     except Exception as e:
#         log_event(f"Database connection failed: {e}")
#         print(f"Database connection failed: {e}")
#         raise

# def test_db_connection():
#     conn = get_db_connection()
#     try:
#         with conn.cursor() as cursor:
#             cursor.execute("SELECT 1")  # Simple query to test the connection
#             result = cursor.fetchone()
#             if result:
#                 print("Connection test successful!")
#             else:
#                 print("Connection test failed.")
#     except Exception as e:
#         log_event(f"Error during connection test: {e}")
#         print(f"Error during connection test: {e}")
#     finally:
#         conn.close()

# if __name__ == "__main__":
#     test_db_connection()




# import os
# import psycopg2
# import logging
# from dotenv import load_dotenv

# # Load environment variables
# load_dotenv()

# # Database Configuration
# DB_CONFIG = {
#     "dbname": os.getenv("DB_NAME"),
#     "user": os.getenv("DB_USER"),
#     "password": os.getenv("DB_PASSWORD"),
#     "host": os.getenv("DB_SERVER"),
#     "port": os.getenv("DB_PORT", "5432"),
# }

# def get_db_connection():
#     """Establish and return a PostgreSQL database connection."""
#     try:
#         conn = psycopg2.connect(**DB_CONFIG)
#         logging.info("Database connection successful.")
#         return conn
#     except psycopg2.Error as e:
#         logging.error(f"Database connection error: {e}")
#         return None

# # Example usage
# if __name__ == "__main__":
#     conn = get_db_connection()
#     if conn:
#         print("Connected to PostgreSQL successfully.")
#         conn.close()

import os
import re
import logging
import httpx
from datetime import datetime

def modify_image_url(image_url):
    """
    Clean the image URL by removing `.transform.*.png` or `.png.png` endings.
    """
    if not image_url or image_url == "N/A":
        return image_url

    # Remove `.transform.*.png` and `.png.png`
    image_url = re.sub(r'\.transform\..*\.png$', '.png', image_url)
    image_url = re.sub(r'\.png\.png$', '.png', image_url)

    return image_url

async def download_image_async(image_url, product_name, timestamp, image_folder, unique_id, retries=3):
    if not image_url or image_url == "N/A":
        return "N/A"

    image_filename = f"{unique_id}_{timestamp}.png"
    image_full_path = os.path.join(image_folder, image_filename)

    # Clean image URL
    image_url = modify_image_url(image_url)

    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(retries):
            try:
                response = await client.get(image_url)
                if response.status_code == 200:
                    with open(image_full_path, "wb") as f:
                        f.write(response.content)
                    return image_full_path
                else:
                    logging.warning(f"Attempt {attempt + 1}/{retries} - Status {response.status_code} downloading image for {product_name}")
            except httpx.RequestError as e:
                logging.warning(f"Attempt {attempt + 1}/{retries} - Error downloading image for {product_name}: {e}")

    logging.error(f"Failed to download image for {product_name} after {retries} attempts.")
    return "N/A"
import asyncio

async def main():
    image_url = "https://www.vancleefarpels.com/content/dam/rcq/vca/16/27/51/1/1627511.png"
    product_name = "Vintage Alhambra"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    image_folder = "images"
    unique_id = "VCA001"

    os.makedirs(image_folder, exist_ok=True)
    result = await download_image_async(image_url, product_name, timestamp, image_folder, unique_id)
    print(f"Saved to: {result}")

asyncio.run(main())
