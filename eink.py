#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import os
import sys
import time
import logging
import firebase_admin
from firebase_admin import credentials, storage, firestore
from PIL import Image, ImageDraw, ImageFont, ImageOps
from datetime import datetime
import schedule
from waveshare_epd import epd7in5_V2  # Ensure you have the correct e-Paper library
from datetime import timedelta
import requests
from zoneinfo import ZoneInfo

# ============================
# Configuration (replace the following with your own settings)
# ============================

CACHE_DIR = '/path/to/your/eink_cache'  # REPLACE with your cache directory path

FONT_PATH = "/path/to/your/fonts/DejaVuSans.ttf"  # REPLACE with your font file path

DISPLAY_WIDTH = 800  # REPLACE with your display's width
DISPLAY_HEIGHT = 480  # REPLACE with your display's height

IMAGE_DISPLAY_DURATION = 300
CLOCK_UPDATE_INTERVAL = 60

FIREBASE_CREDENTIALS_PATH = "/path/to/your/firebase-service-account.json"  # REPLACE with your Firebase credentials path
FIREBASE_BUCKET_NAME = "your-firebase-bucket-name.appspot.com"  # REPLACE with your Firebase Storage bucket name
FOLDER_NAME = "your_photos_folder"  # REPLACE with your Firebase Storage folder name

SETTINGS_COLLECTION = "settings"  # REPLACE if using a different collection name
SETTINGS_DOCUMENT = "display_settings"  # REPLACE if using a different document name

# ============================
# Initialize Logging
# ============================

logging.basicConfig(level=logging.INFO)

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# ============================
# Initialize Firebase
# ============================

try:
    cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    firebase_admin.initialize_app(cred, {"storageBucket": FIREBASE_BUCKET_NAME})
    logging.info("Firebase initialized successfully.")
except Exception as e:
    logging.error(f"Failed to initialize Firebase: {e}")
    sys.exit(1)

bucket = storage.bucket()

db_firestore = firestore.client()

# ============================
# Initialize e-Paper Display
# ============================

try:
    logging.info("Initializing e-Paper display...")
    epd = epd7in5_V2.EPD()
    epd.init()
    epd.Clear()
    logging.info("e-Paper display initialized.")
except Exception as e:
    logging.error(f"Failed to initialize e-Paper display: {e}")
    sys.exit(1)

# ============================
# Load Fonts
# ============================

try:
    font_large = ImageFont.truetype(FONT_PATH, 36)
    font_small = ImageFont.truetype(FONT_PATH, 24)
    font_xlarge = ImageFont.truetype(FONT_PATH, 72)
    logging.info("Fonts loaded successfully.")
except IOError:
    logging.error("Font file not found. Please check FONT_PATH.")
    sys.exit(1)

# ============================
# Helper Functions
# ============================

def list_firebase_images(folder_name):
    """List all images in the specified folder in Firebase Storage."""
    try:
        blobs = bucket.list_blobs(prefix=folder_name)
        images = []
        for blob in blobs:
            if blob.content_type and blob.content_type.startswith("image/"):
                url = blob.generate_signed_url(expiration=timedelta(hours=1))
                images.append({'name': blob.name, 'url': url})
        logging.info(f"Found {len(images)} images in Firebase Storage folder '{folder_name}'.")
        return images
    except Exception as e:
        logging.error(f"Failed to list images from Firebase Storage: {e}")
        return []

def download_image(url, local_path):
    """Download image from URL to local_path."""
    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            logging.info(f"Downloaded image to {local_path}")
            return True
        else:
            logging.error(f"Failed to download image from {url}, status code {response.status_code}")
            return False
    except Exception as e:
        logging.error(f"Exception while downloading image from {url}: {e}")
        return False

def get_cached_image(blob_name, url):
    """Get cached image path, download if not present."""
    local_filename = os.path.basename(blob_name)
    local_path = os.path.join(CACHE_DIR, local_filename)
    if not os.path.exists(local_path):
        success = download_image(url, local_path)
        if not success:
            return None
    return {'name': blob_name, 'path': local_path}

def get_display_settings():
    """Fetch display settings from Firestore."""
    try:
        settings_doc_ref = db_firestore.collection(SETTINGS_COLLECTION).document(SETTINGS_DOCUMENT)
        settings_doc = settings_doc_ref.get()
        if settings_doc.exists:
            settings_data = settings_doc.to_dict()
            show_clock = settings_data.get("show_clock", True)
            show_photos = settings_data.get("show_photos", True)
            timezone_str = settings_data.get("timezone", "UTC")
            logging.info(f"Display Settings - Clock: {show_clock}, Photos: {show_photos}, Timezone: {timezone_str}")
            return show_clock, show_photos, timezone_str
        else:
            default_settings = {
                "show_clock": True,
                "show_photos": True,
                "timezone": "UTC"
            }
            settings_doc_ref.set(default_settings)
            logging.info("Created default display settings.")
            return default_settings["show_clock"], default_settings["show_photos"], default_settings["timezone"]
    except Exception as e:
        logging.error(f"Error fetching display settings from Firestore: {e}")
        return True, True, "UTC"

def resize_image(image_path, display_width, display_height):
    """Resize image based on orientation and add padding if necessary."""
    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        img_width, img_height = img.size
        logging.info(f"Original Image Size: {img_width}x{img_height}")

        if img_width > img_height:
            img = img.resize((display_width, display_height), Image.ANTIALIAS)
            logging.info(f"Resized Horizontal Image to: {display_width}x{display_height}")
        else:
            aspect_ratio = img_width / img_height
            new_height = display_height
            new_width = int(aspect_ratio * new_height)

            if new_width > display_width:
                new_width = display_width
                new_height = int(new_width / aspect_ratio)
                img = img.resize((new_width, new_height), Image.ANTIALIAS)
                logging.info(f"Adjusted Resized Vertical Image to: {new_width}x{new_height}")
            else:
                img = img.resize((new_width, new_height), Image.ANTIALIAS)
                logging.info(f"Resized Vertical Image to: {new_width}x{new_height}")

            new_img = Image.new('RGB', (display_width, display_height), 'white')
            x_offset = (display_width - new_width) // 2
            y_offset = (display_height - new_height) // 2
            new_img.paste(img, (x_offset, y_offset))
            img = new_img
            logging.info(f"Pasted Vertical Image on White Background: {display_width}x{display_height}")

        img = img.convert('1')
        logging.info(f"Final Image Size (1-bit): {img.size}")
        return img
    except Exception as e:
        logging.error(f"Error resizing image {image_path}: {e}")
        return None

def overlay_clock(image, timezone_str, centered=False):
    """Overlay current time on the image in the specified timezone."""
    try:
        draw = ImageDraw.Draw(image)
        tz = ZoneInfo(timezone_str)
        current_time = datetime.now(tz).strftime('%I:%M %p')

        if centered:
            font = font_xlarge
            text_width, text_height = draw.textsize(current_time, font=font)
            x = (DISPLAY_WIDTH - text_width) // 2
            y = (DISPLAY_HEIGHT - text_height) // 2
        else:
            font = font_large
            text_width, text_height = draw.textsize(current_time, font=font)
            x = DISPLAY_WIDTH - text_width - 10
            y = 10

        draw.rectangle([(x - 5, y - 5), (x + text_width + 5, y + text_height + 5)], fill=255)
        draw.text((x, y), current_time, font=font, fill=0)
        logging.info(f"Overlayed clock at position: ({x}, {y}) with font size: {font.size}")
        return image
    except Exception as e:
        logging.error(f"Error overlaying clock: {e}")
        return image

# ============================
# ImageDisplayer Class
# ============================

class ImageDisplayer:
    def __init__(self, epd, image_info_list):
        """
        Initialize the ImageDisplayer.

        :param epd: The e-Paper display instance.
        :param image_info_list: List of dictionaries containing 'name' and 'url' of images.
        """
        self.epd = epd
        self.image_info_list = image_info_list
        self.current_index = 0
        self.cached_images = []
        self.show_clock = True
        self.show_photos = True
        self.timezone_str = "UTC"
        self.prepare_images()

    def prepare_images(self):
        """Pre-download all images and cache them."""
        logging.info("Preparing images...")
        for image_info in self.image_info_list:
            blob_name = image_info['name']
            url = image_info['url']
            cached_image = get_cached_image(blob_name, url)
            if cached_image:
                self.cached_images.append(cached_image)
        if not self.cached_images:
            logging.error("No images available to display.")
            sys.exit(1)
        logging.info(f"{len(self.cached_images)} images cached.")

    def update_settings(self):
        """Fetch and update display settings from Firestore."""
        self.show_clock, self.show_photos, self.timezone_str = get_display_settings()
        logging.info(f"Updated Settings - Clock: {self.show_clock}, Photos: {self.show_photos}, Timezone: {self.timezone_str}")

    def display_image(self):
        """Display the current image with clock overlay if enabled."""
        if not self.show_photos:
            logging.info("Photo display is disabled.")
            if self.show_clock:
                img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 255)
                img = overlay_clock(img, self.timezone_str, centered=True)
                try:
                    self.epd.display(self.epd.getbuffer(img))
                    logging.info("Displayed clock only.")
                except Exception as e:
                    logging.error(f"Failed to display clock: {e}")
            else:
                self.epd.Clear()
                logging.info("Cleared display as both clock and photos are disabled.")
            return

        current_image_info = self.cached_images[self.current_index]
        img_path = current_image_info['path']
        blob_name = current_image_info['name']
        img = resize_image(img_path, DISPLAY_WIDTH, DISPLAY_HEIGHT)
        if img is None:
            logging.error(f"Failed to process image {img_path}")
            return
        if self.show_clock:
            img = overlay_clock(img, self.timezone_str, centered=False)
        try:
            self.epd.display(self.epd.getbuffer(img))
            logging.info(f"Displayed image {self.current_index + 1}/{len(self.cached_images)}")
            update_current_photo(blob_name)
        except Exception as e:
            logging.error(f"Failed to display image: {e}")

    def next_image(self):
        """Move to the next image and update settings."""
        self.update_settings()
        if not self.show_photos:
            return
        self.current_index = (self.current_index + 1) % len(self.cached_images)
        self.display_image()

# ============================
# Firestore Update Function
# ============================

def update_current_photo(blob_name):
    """Update the current_photo field in Firestore."""
    try:
        settings_doc_ref = db_firestore.collection(SETTINGS_COLLECTION).document(SETTINGS_DOCUMENT)
        blob = bucket.blob(blob_name)
        if blob.exists():
            current_photo_url = blob.generate_signed_url(expiration=timedelta(hours=1))
            settings_doc_ref.update({"current_photo": current_photo_url})
            logging.info(f"Updated current_photo to {current_photo_url}")
        else:
            logging.error(f"Blob for image {blob_name} does not exist in Firebase Storage.")
    except Exception as e:
        logging.error(f"Failed to update current_photo in Firestore: {e}")

# ============================
# Main Function
# ============================

def main():
    image_info_list = list_firebase_images(FOLDER_NAME)
    if not image_info_list:
        logging.error("No image URLs found. Exiting.")
        sys.exit(1)

    displayer = ImageDisplayer(epd, image_info_list)
    displayer.display_image()

    def schedule_tasks():
        displayer.update_settings()
        if displayer.show_photos:
            schedule.every(IMAGE_DISPLAY_DURATION).seconds.do(displayer.next_image)
        if displayer.show_clock:
            schedule.every(CLOCK_UPDATE_INTERVAL).seconds.do(displayer.display_image)

    schedule_tasks()

    logging.info("Starting main loop...")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Exiting program...")
        epd.Clear()
        epd.sleep()
        sys.exit(0)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        epd.Clear()
        epd.sleep()
        sys.exit(1)

if __name__ == "__main__":
    main()