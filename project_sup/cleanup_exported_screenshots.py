"""
project_sup/cleanup_exported_screenshots.py
───────────────────────────────────────────
Cleans up duplicate screenshot files from output/screenshots/
that belong to domains already marked as exported: True in MongoDB.

Preserves any screenshots for unexported/pending domains.

Usage:
    python project_sup/cleanup_exported_screenshots.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, get_db
from export_domains.screenshot import all_filename_candidates, _url_to_filename


def cleanup_exported_screenshots(screenshots_dir: str = None):
    get_db()

    if screenshots_dir is None:
        screenshots_dir = os.getenv("SCREENSHOT_DIR", str(PROJECT_ROOT / "output" / "screenshots"))

    src_path = Path(screenshots_dir)
    if not src_path.exists():
        print(f"[!] Screenshot directory not found: {src_path}")
        return

    # 1. Collect all screenshot files currently in output/screenshots/
    image_files = [
        f for f in src_path.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
    ]

    total_images = len(image_files)
    print(f"\n[+] Total screenshots currently in {src_path}: {total_images:,}")

    if total_images == 0:
        print("[+] No screenshot files to clean.")
        return

    # 2. Build set of filename patterns for all exported: True domains
    print("[+] Loading exported domains from MongoDB...")
    exported_docs = list(checked_domains().find(
        {"exported": True},
        {"_id": 1, "domain": 1, "url": 1}
    ))
    print(f"[+] Found {len(exported_docs):,} domains marked as exported: True in MongoDB.")

    exported_filenames = set()
    for doc in exported_docs:
        dom = str(doc.get("domain") or doc.get("_id"))
        url = str(doc.get("url") or f"https://{dom}")
        for cand in all_filename_candidates(url, dom):
            exported_filenames.add(cand.lower())
        clean_dom = dom.removeprefix("www.")
        exported_filenames.add(_url_to_filename(clean_dom).lower())
        exported_filenames.add(_url_to_filename(dom).lower())

    # 3. Clean up duplicates
    removed_count = 0
    kept_count = 0

    print("\n[+] Removing already-exported duplicate screenshots from output/screenshots/...")
    for img_file in tqdm(image_files, desc="Cleaning Duplicates", unit="img"):
        if img_file.name.lower() in exported_filenames:
            try:
                img_file.unlink()
                removed_count += 1
            except Exception as e:
                tqdm.write(f"  [!] Failed to remove {img_file.name}: {e}")
        else:
            kept_count += 1

    # 4. Print Summary
    print("\n" + "=" * 65)
    print("       EXPORTED SCREENSHOT DUPLICATE CLEANUP SUMMARY")
    print("=" * 65)
    print(f"  Total Screenshots Audited      : {total_images:,}")
    print(f"  Removed (Already Exported)     : {removed_count:,}")
    print(f"  Kept (Unexported / Pending)    : {kept_count:,}")
    print(f"  Active Screenshots Directory   : {src_path}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    cleanup_exported_screenshots()
