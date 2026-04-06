import os
import cloudinary
import cloudinary.api
import cloudinary.uploader
from typing import Optional

def upload_file(local_path: str, resource_type: str = "auto") -> Optional[str]:
    """
    Uploads a local file to Cloudinary and returns the permanent secure URL (HTTPS).
    Deletes the local file afterward to save disk space on ephemeral tiers.
    If CLOUDINARY_URL is not set, returns None so the app can fallback to local serving.
    """
    url = os.environ.get("CLOUDINARY_URL")
    if not url:
        print("[CloudStorage] Warning: CLOUDINARY_URL not set. Falling back to local storage.")
        return None

    # Manually parse the URL to bypass buggy cloudinary string parser
    try:
        clean = url.replace("cloudinary://", "")
        key_secret, cloud_name = clean.split("@")
        api_key, api_secret = key_secret.split(":")
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret
        )
    except Exception as e:
        print(f"[CloudStorage] Config warning: {e}")

    try:
        print(f"[CloudStorage] Uploading {local_path}...")
        response = cloudinary.uploader.upload(local_path, resource_type=resource_type)
        secure_url = response.get("secure_url")
        
        # Cleanup the ephemeral local disk
        if secure_url and os.path.exists(local_path):
            try:
                os.remove(local_path)
                print(f"[CloudStorage] Upload successful. Cleaned up {local_path}.")
            except OSError as e:
                print(f"[CloudStorage] Warning: Could not delete {local_path} - {e}")
                
        return secure_url
    except Exception as e:
        print(f"[CloudStorage] Error uploading {local_path}: {e}")
        return None
