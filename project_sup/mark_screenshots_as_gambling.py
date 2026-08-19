"""
project_sup/mark_screenshots_as_gambling.py — Mark all screenshot domains as Gambling & screenshot_taken=True.

Scans output/screenshots/ for existing screenshot image files on disk, extracts/matches
their domain names against MongoDB, and updates their status to 'gambling' with
screenshot_taken=True.

Usage:
    python project_sup/mark_screenshots_as_gambling.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, write_result, get_db
from export_domains.screenshot import _url_to_filename


def extract_domain_from_filename(filename: str) -> str:
    """Fallback domain extractor from screenshot filename stem."""
    stem = Path(filename).stem
    prefix = stem.rsplit("_", 1)[0] if "_" in stem else stem
    return prefix.replace("www-", "").replace("_", ".").strip().removeprefix("www.")


def mark_screenshots_as_gambling():
    print("[support] Initializing Screenshot Gambling Marker...")
    get_db()

    screenshots_dir = Path(os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots")))
    if not screenshots_dir.exists():
        print(f"[support] Screenshot directory not found at {screenshots_dir}")
        return

    image_files = list(screenshots_dir.rglob("*.jpg")) + list(screenshots_dir.rglob("*.png"))
    if not image_files:
        print("[support] No screenshot images found.")
        return

    print(f"[support] Found {len(image_files)} screenshots in {screenshots_dir}")
    print("[support] Building domain lookup map from MongoDB...")

    # Build bidirectional lookup map matching exact DB domain records
    filename_map = {}
    known_domains = set()

    try:
        for collection in [checked_domains(), source_domains()]:
            for doc in collection.find({}, {"_id": 1, "domain": 1}):
                d = (doc.get("domain") or doc.get("_id") or "").strip().lower()
                if d and "." in d:
                    clean_d = d.removeprefix("www.")
                    known_domains.add(clean_d)
                    for u in [f"https://{clean_d}", f"http://{clean_d}", f"https://www.{clean_d}"]:
                        fname = _url_to_filename(u)
                        filename_map[fname] = clean_d
                        filename_map[Path(fname).stem] = clean_d
    except Exception as e:
        print(f"[support] Warning: MongoDB lookup map pre-build failed ({e}). Using fallback parser.")

    updated_count = 0
    stats = {"gambling": 0, "failed": 0}

    print(f"[support] Processing {len(image_files)} screenshot domains...")
    pbar = tqdm(total=len(image_files), desc="Updating Status", unit="img")

    for img_path in image_files:
        stem = img_path.stem
        filename = img_path.name
        
        # 1. Match from MongoDB exact lookup map
        domain = filename_map.get(filename) or filename_map.get(stem)
        
        # 2. Fallback to name parser if not found in map
        if not domain:
            domain = extract_domain_from_filename(filename)

        if not domain or "." not in domain:
            stats["failed"] += 1
            pbar.update(1)
            continue

        try:
            write_result(
                domain,
                url=f"https://{domain}",
                status="gambling",
                reason="Confirmed gambling site with screenshot on disk",
                screenshot_taken=True,
                screenshot_failed_reason=None
            )
            updated_count += 1
            stats["gambling"] += 1
            tqdm.write(f"  [+] Marked Gambling: {domain} (screenshot_taken=True)")
        except Exception as e:
            tqdm.write(f"  [!] Failed to update {domain}: {e}")
            stats["failed"] += 1

        pbar.update(1)

    pbar.close()
    print(f"\n{'='*55}")
    print("      SCREENSHOT DOMAIN GAMBLING STATUS UPDATE COMPLETE")
    print(f"{'='*55}")
    print(f" Total Screenshots Audited : {len(image_files)}")
    print(f" Updated to 'gambling'    : {updated_count}")
    print(f" Failed / Unrecognized     : {stats['failed']}")
    print(f" screenshot_taken          : True")
    print(f"{'='*55}")


if __name__ == "__main__":
    mark_screenshots_as_gambling()
