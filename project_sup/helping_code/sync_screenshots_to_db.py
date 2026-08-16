"""
project_sup/helping_code/sync_screenshots_to_db.py — Sync disk screenshots in output/screenshots/ with MongoDB.

Workflow:
1. Scan output/screenshots/ for valid .jpg and .png images.
2. Match each image file to its domain/URL.
3. Update MongoDB checked_domains for each present screenshot using bulk operations:
   - status = "gambling"
   - screenshot_taken = True
   - screenshot_failed_reason = None
   - exported = False
4. Update source_domains (domain_Listed):
   - processed = True
   - active = True
5. Print clean summary statistics.
"""
import sys
import os
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import UpdateOne

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

from db.mongo_client import get_db, checked_domains, source_domains
from capture_url.screenshot import _url_to_filename, is_valid_screenshot

SCREENSHOTS_DIR = ROOT_DIR / "output" / "screenshots"
IST = timezone(timedelta(hours=5, minutes=30))


def infer_domain_from_filename(filename: str) -> str:
    """Fallback helper to extract domain from filename e.g. '101-gamee_net_57e8b32d.jpg' -> '101-gamee.net'."""
    name_without_ext = os.path.splitext(filename)[0]
    parts = name_without_ext.split('_')
    if len(parts) > 1 and len(parts[-1]) == 8 and all(c in '0123456789abcdefABCDEF' for c in parts[-1]):
        parts = parts[:-1]
    
    clean_str = "_".join(parts)
    clean_str = re.sub(r'_([a-z0-9]+)$', r'.\1', clean_str)
    clean_str = clean_str.replace('_', '.')
    return clean_str


def build_filename_to_doc_map(image_files: list[Path]) -> dict:
    """Build a mapping from screenshot filename -> MongoDB checked_domains doc instantly."""
    get_db()
    mapping = {}
    inferred_domains = set()
    img_to_domain = {}

    for img_path in image_files:
        domain = infer_domain_from_filename(img_path.name)
        inferred_domains.add(domain)
        img_to_domain[img_path.name] = domain

    docs = checked_domains().find({"_id": {"$in": list(inferred_domains)}})
    docs_by_id = {doc["_id"]: doc for doc in docs}

    for img_path in image_files:
        fn = img_path.name
        domain = img_to_domain[fn]
        doc = docs_by_id.get(domain)
        if doc:
            url = doc.get("url") or f"https://{domain}"
            fn1 = _url_to_filename(url)
            fn2 = _url_to_filename(f"https://{domain}")
            fn3 = _url_to_filename(f"http://{domain}")
            mapping[fn1] = doc
            mapping[fn2] = doc
            mapping[fn3] = doc

    return mapping


def sync_screenshots_to_db() -> dict:
    get_db()
    if not SCREENSHOTS_DIR.exists():
        print(f"[sync] Directory {SCREENSHOTS_DIR} does not exist.")
        return {"total": 0, "synced": 0, "invalid": 0}

    image_files = list(SCREENSHOTS_DIR.glob("*.jpg")) + list(SCREENSHOTS_DIR.glob("*.png"))
    total_images = len(image_files)
    print(f"\n[sync] Found {total_images} screenshot(s) in {SCREENSHOTS_DIR.relative_to(ROOT_DIR)} to sync with MongoDB...")

    if total_images == 0:
        return {"total": 0, "synced": 0, "invalid": 0}

    fn_map = build_filename_to_doc_map(image_files)
    checked_ops = []
    source_ops = []
    synced_count = 0
    invalid_count = 0

    for img_path in image_files:
        fn = img_path.name

        if not is_valid_screenshot(str(img_path)):
            invalid_count += 1
            continue

        doc = fn_map.get(fn)
        if doc:
            domain = doc.get("domain") or doc.get("_id")
            url = doc.get("url") or f"https://{domain}"
        else:
            domain = infer_domain_from_filename(fn)
            url = f"https://{domain}"

        checked_ops.append(
            UpdateOne(
                {"_id": domain},
                {
                    "$set": {
                        "domain": domain,
                        "url": url,
                        "status": "gambling",
                        "screenshot_taken": True,
                        "screenshot_failed_reason": None,
                        "exported": False,
                    }
                },
                upsert=True,
            )
        )

        source_ops.append(
            UpdateOne(
                {"_id": domain},
                {"$set": {"processed": True, "active": True}},
            )
        )

        synced_count += 1

    updated_db_docs = 0
    if checked_ops:
        res1 = checked_domains().bulk_write(checked_ops, ordered=False)
        updated_db_docs = res1.modified_count + res1.upserted_count
    if source_ops:
        source_domains().bulk_write(source_ops, ordered=False)

    summary = {
        "total": total_images,
        "synced": synced_count,
        "invalid": invalid_count,
        "updated_db_docs": updated_db_docs,
    }

    print("=========================================================")
    print("           SCREENSHOT SYNC TO MONGODB COMPLETE           ")
    print("=========================================================")
    print(f" Total Screenshots Found  : {total_images:,}")
    print(f" Valid Screenshots Synced : {synced_count:,}")
    print(f" Invalid / Corrupt Skipped: {invalid_count:,}")
    print(f" MongoDB Documents Updated: {updated_db_docs:,}")
    print("=========================================================\n")

    return summary


if __name__ == "__main__":
    sync_screenshots_to_db()
