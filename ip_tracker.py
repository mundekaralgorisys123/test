# import json
# import os
# from threading import Lock

# IP_FILE = 'ip_usage.json'
# DEFAULT_LIMIT = 2000  # Default limit for each website
# lock = Lock()

# # Load or initialize the IP usage data
# def load_ip_data():
#     if os.path.exists(IP_FILE):
#         try:
#             with open(IP_FILE, 'r') as f:
#                 return json.load(f)
#         except json.JSONDecodeError:
#             # If JSON is invalid, reset the file
#             print("Corrupted JSON file detected. Resetting...")
#             return {}
#     else:
#         return {}

# # Save the updated IP usage data
# def save_ip_data(data):
#     with lock:
#         with open(IP_FILE, 'w') as f:
#             json.dump(data, f, indent=4)

# # Check and update the usage for an IP
# def check_and_update_usage(ip_address, domain, requested_pages):
#     ip_data = load_ip_data()

#     # Initialize new IP if not present
#     if ip_address not in ip_data:
#         ip_data[ip_address] = {}

#     # Initialize domain usage if not present
#     if domain not in ip_data[ip_address]:
#         ip_data[ip_address][domain] = {"usage": 0, "limit": DEFAULT_LIMIT}

#     # Calculate new usage
#     site_usage = ip_data[ip_address][domain]
#     new_usage = site_usage["usage"] + requested_pages

#     # Check if the requested pages exceed the limit
#     if new_usage > site_usage["limit"]:
#         print(f"Limit exceeded for {domain} by IP: {ip_address}")
#         return False  # Exceeds limit

#     # Update usage and save
#     ip_data[ip_address][domain]["usage"] = new_usage
#     save_ip_data(ip_data)
#     print(f"Updated usage for {domain} by IP: {ip_address}. New usage: {new_usage}")
#     return True



# CREATE TABLE scraping_settings (
#     id INT IDENTITY(1,1) PRIMARY KEY,
#     setting_name VARCHAR(100) NOT NULL UNIQUE,
#     daily_limit INT NOT NULL,
#     products_fetched_today INT DEFAULT 0,
#     last_reset DATE NOT NULL,
#     is_disabled BIT DEFAULT 0  -- Use 0 for FALSE
# );


# INSERT INTO scraping_settings (
#     setting_name,
#     daily_limit,
#     products_fetched_today,
#     last_reset,
#     is_disabled
# ) VALUES (
#     'daily_product_limit',  -- Setting name for combined limit
#     4000,                   -- Total daily limit of 4000 products
#     0,                      -- Start with 0 products fetched today
#     GETDATE(),              -- Set today's date as last reset
#     0                       -- Not disabled by default
# );



# UPDATE [Webstudy].[dbo].[scraping_settings] 
# SET [daily_limit] = 300
# WHERE [setting_name] = 'daily_product_limit';
