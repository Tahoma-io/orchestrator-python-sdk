import io
import uuid
import fsspec
from PIL import Image
from dotenv import load_dotenv
import os

load_dotenv()

# Storage configuration (can be moved to environment variables later)
STORAGE_OPTIONS = {
    "account_name": os.getenv("AZURE_STORAGE_ACCOUNT_NAME"),
    "account_key": os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
}
PROTOCOL = "az"
CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER")
CDN_BASE_URL = os.getenv("AZURE_CDN_BASE_URL")

def upload_screenshot(image: Image.Image, prefix: str = "screenshot") -> str:
    """
    Saves a Pillow Image object to Azure Blob Storage and returns the CDN link.
    """
    # 1. Generate a unique filename
    file_uuid = str(uuid.uuid4())
    filename = f"{prefix}_{file_uuid}.png"
    storage_path = f"{PROTOCOL}://{CONTAINER}/{filename}"

    # 2. Convert Image to bytes
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()

    # 3. Upload using fsspec
    try:
        with fsspec.open(storage_path, "wb", **STORAGE_OPTIONS) as f:
            f.write(img_bytes)
    except Exception as e:
        print(f"⚠️ Failed to upload screenshot to {PROTOCOL}: {e}")
        raise

    # 4. Return the formatted CDN link
    return f"{CDN_BASE_URL}/{CONTAINER}/{filename}"
