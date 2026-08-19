"""
project_sup/cleanup_false_positives.py — OCR-based false positive auditor.

Reads the actual text in every screenshot via EasyOCR, detecting:
  1. HTTP 403 / Cloudflare WAF block pages
  2. DNS error / "This site can't be reached" pages
  3. Domain parking / for-sale landers (GoDaddy, Sedo, Dan.com, etc.)

For any false positive: deletes the image and updates MongoDB.

Usage:
    python project_sup/cleanup_false_positives.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, write_result, get_db
from export_domains.screenshot import delete_screenshot

_BLOCKED_IMAGE_MARKERS = [
    "403 forbidden", "you have been blocked", "access denied", "just a moment",
    "why have been blocked", "cloudflare", "checking your browser",
    "attention required", "error 403", "403 - forbidden", "forbidden nginx", "security check",
]

_DEAD_OR_PARKED_IMAGE_MARKERS = [
    "this site can't be reached", "this site cannot be reached", "dns_probe_finished",
    "err_name_not_resolved", "server ip address could not be found", "is for sale",
    "buy this domain", "this domain is for sale", "domain is available for sale",
    "domain for sale", "make an offer on this domain", "parked domain", "parked free",
    "sedo.com", "sedoparking", "godaddy", "hugedomains", "dan.com", "atom.com",
    "parkingcrew", "site under construction", "domain expired",
]


def extract_domain_from_filename(filename: str) -> str:
    """Extract clean domain from screenshot filename (e.g. 12-bet_in_76d87080.jpg -> 12-bet.in)."""
    stem = Path(filename).stem
    prefix = stem.rsplit("_", 1)[0] if "_" in stem else stem
    return prefix.replace("www-", "").replace("_", ".").strip().removeprefix("www.")


def run_cleanup():
    print("[cleanup] Initializing OCR False Positive Auditor...")
    get_db()

    screenshots_dir = Path(os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots")))
    if not screenshots_dir.exists():
        print(f"[cleanup] Screenshot directory not found at {screenshots_dir}")
        return

    image_files = list(screenshots_dir.rglob("*.jpg")) + list(screenshots_dir.rglob("*.png"))
    if not image_files:
        print("[cleanup] No screenshots found.")
        return

    print(f"[cleanup] Found {len(image_files)} screenshots. Loading EasyOCR...")
    import easyocr
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    stats = {"blocked": 0, "dead": 0, "valid": 0}
    pbar = tqdm(total=len(image_files), desc="Auditing Screenshots", unit="img")

    for img_path in image_files:
        path_str = str(img_path)
        dom = extract_domain_from_filename(path_str)
        try:
            img_text = " ".join(reader.readtext(path_str, detail=0)).lower()
        except Exception:
            img_text = ""

        verdict = None
        reason = ""
        if any(m in img_text for m in _BLOCKED_IMAGE_MARKERS):
            verdict, reason = "blocked", "Blocked: 403/Cloudflare WAF image detected via OCR"
        elif any(m in img_text for m in _DEAD_OR_PARKED_IMAGE_MARKERS):
            verdict, reason = "dead", "Dead: DNS error or Parked/For-Sale lander detected via OCR"

        if verdict:
            delete_screenshot(dom, output_dir=str(screenshots_dir))
            if os.path.exists(path_str):
                try:
                    os.remove(path_str)
                except Exception:
                    pass
            write_result(dom, url=f"https://{dom}", status=verdict,
                         reason=f"[cleanup] {reason}", screenshot_taken=False,
                         screenshot_failed_reason=reason)
            stats[verdict] += 1
            tqdm.write(f"  [x] {verdict.capitalize()}: {dom} -> deleted, marked '{verdict}'")
        else:
            stats["valid"] += 1
        pbar.update(1)

    pbar.close()
    print(f"\n{'='*55}")
    print("           FALSE POSITIVE AUDIT SUMMARY")
    print(f"{'='*55}")
    print(f" Total Audited  : {len(image_files)}")
    print(f" Valid Retained : {stats['valid']}")
    print(f" Cleaned 403    : {stats['blocked']}")
    print(f" Cleaned Parked : {stats['dead']}")
    print(f"{'='*55}")


if __name__ == "__main__":
    run_cleanup()
