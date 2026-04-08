import sys
import argparse
from pathlib import Path
import uuid
import os

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

import boto3
from botocore.config import Config
import httpx
from sqlalchemy import select, update

from app.DB.main import SessionLocal
from app.DB.schema import Events
from app.config import config


OLD_IMAGE_BASE_URL = "https://refactor.albrrak773.com/files"


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def download_image(key: str) -> tuple[bytes, str | None]:
    url = f"{OLD_IMAGE_BASE_URL}/{key}"
    print(f"  Downloading from {url}")
    resp = httpx.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type")


def upload_to_r2(client, key: str, content: bytes, content_type: str | None) -> str:
    client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=content_type)
    return f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"


def main(dry_run: bool):
    r2_client = get_r2_client()
    with SessionLocal() as session:
        events = session.scalars(select(Events).where(Events.image_url.isnot(None))).all()

        migrated = 0
        failed = 0
        skipped = 0

        for event in events:
            if not event.image_url:
                continue

            image_url = event.image_url

            if image_url.startswith("http"):
                print(f"Event {event.id} ({event.name}): already migrated, skipping")
                skipped += 1
                continue

            print(f"Event {event.id} ({event.name}): migrating {image_url}")

            try:
                content, content_type = download_image(image_url)

                ext = os.path.splitext(image_url)[1] if "." in image_url else ""
                new_key = f"event-images/{uuid.uuid4()}{ext}"

                new_url = upload_to_r2(r2_client, new_key, content, content_type)

                print(f"  Uploaded to {new_url}")

                if not dry_run:
                    session.execute(update(Events).where(Events.id == event.id).values(image_url=new_url))

                migrated += 1

            except Exception as e:
                print(f"  FAILED: {e}")
                failed += 1

        if not dry_run:
            session.commit()
            print(f"\nDone: {migrated} migrated, {failed} failed, {skipped} skipped.")
        else:
            session.rollback()
            print(f"\nDry run: {migrated} would be migrated, {failed} failed, {skipped} skipped (no changes applied).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate event images from VPS to R2")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")
    args = parser.parse_args()
    try:
        main(args.dry_run)
    except ValueError as e:
        if "R2_" in str(e):
            print("Error: R2 environment variables not set.")
            print("\nRun with infisical to load secrets:")
            print("  infisical run --path=/admin-backend --env dev -- uv run scripts/backfill_images_to_r2.py")
            sys.exit(1)
        raise
