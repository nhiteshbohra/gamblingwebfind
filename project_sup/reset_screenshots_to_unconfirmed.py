"""
project_sup/reset_screenshots_to_unconfirmed.py — Reset screenshot domains back to 'needs_ai'.

Deletes all screenshot files in output/screenshots/ and resets their MongoDB status to
'needs_ai' (unconfirmed) so they are re-evaluated from scratch on the next run.

Usage:
    python project_sup/reset_screenshots_to_unconfirmed.py
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
from export_domains.screenshot import delete_screenshot


def extract_domain_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    prefix = stem.rsplit("_", 1)[0] if "_" in stem else stem
    return prefix.replace("www-", "").replace("_", ".").strip().removeprefix("www.")


def reset_screenshots():
    print("[reset] Initializing Screenshot Domain Reset...")
    get_db()

    screenshots_dir = Path(os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots")))
    if not screenshots_dir.exists():
        print(f"[reset] Screenshot directory not found at {screenshots_dir}")
        return

    image_files = list(screenshots_dir.rglob("*.jpg")) + list(screenshots_dir.rglob("*.png"))
    if not image_files:
        print("[reset] No screenshot image files found.")
        return

    print(f"[reset] Found {len(image_files)} screenshot images on disk.")

    domain_set = set()
    domain_img_map = {}
    for img_path in image_files:
        dom = extract_domain_from_filename(str(img_path))
        if dom and "." in dom:
            domain_img_map[dom] = str(img_path)
            domain_set.add(dom)

    try:
        cursor = checked_domains().find(
            {"$or": [{"status": "gambling"}, {"screenshot_taken": True}]},
            {"_id": 1, "domain": 1}
        )
        for doc in cursor:
            d = doc.get("domain") or doc.get("_id")
            if d:
                domain_set.add(d.lower().strip().removeprefix("www."))
    except Exception as e:
        print(f"[reset] Note: Could not query MongoDB ({e})")

    print(f"[reset] Resetting {len(domain_set)} unique domains to 'needs_ai' (unconfirmed)...")

    reset_count = 0
    pbar = tqdm(total=len(domain_set), desc="Resetting Domains", unit="dom")
    for dom in domain_set:
        delete_screenshot(dom, output_dir=str(screenshots_dir))
        direct_path = domain_img_map.get(dom)
        if direct_path and os.path.exists(direct_path):
            try:
                os.remove(direct_path)
            except Exception:
                pass
        checked_domains().update_one(
            {"_id": dom},
            {"$set": {
                "domain": dom,
                "status": "needs_ai",
                "screenshot_taken": False,
                "screenshot_failed_reason": "Reset to unconfirmed queue by user",
                "reason": "Reset to unconfirmed queue for fresh re-evaluation",
                "ai_evaluated": False,
            }},
            upsert=True
        )
        reset_count += 1
        pbar.update(1)

    pbar.close()
    print(f"\n{'='*55}")
    print("        SCREENSHOT DOMAIN RESET COMPLETE")
    print(f"{'='*55}")
    print(f" Total Domains Reset to 'needs_ai' : {reset_count}")
    print(f" Status in MongoDB updated to       : 'needs_ai' (unconfirmed)")
    print(f"{'='*55}")


if __name__ == "__main__":
    reset_screenshots()
