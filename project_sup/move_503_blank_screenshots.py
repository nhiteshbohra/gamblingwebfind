"""
project_sup/move_503_blank_screenshots.py
──────────────────────────────────────────
Audits all screenshots in output/screenshots/ for:
  1. Blank / solid-color / unrendered images (variance < 2.5)
  2. Domain Parking & For-Sale landers (GoDaddy, Sedo, Dan.com, HugeDomains, Afternic, etc.)
  3. HTTP 502 / 503 / 504 server error pages
  4. HTTP 403 / Cloudflare WAF block pages
  5. DNS error & "This site can't be reached" pages

For every detected false positive:
  • Moves the screenshot into the quarantine folder (output/503error/)
  • Updates MongoDB checked_domains and domain_Listed collections in high-performance batches.

Usage:
    python project_sup/move_503_blank_screenshots.py
    python project_sup/move_503_blank_screenshots.py --skip-ocr   (blank check only)
"""

import os
import sys
import shutil
import argparse
from pathlib import Path
from PIL import Image, ImageStat
from dotenv import load_dotenv
from tqdm import tqdm
from pymongo import UpdateOne

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, get_db
from export_domains.screenshot import is_valid_screenshot, all_filename_candidates, _url_to_filename

# ── Text Markers for OCR Detection ───────────────────────────────────────────

_PARKED_OR_FOR_SALE_MARKERS = [
    "godaddy", "godaddy auctions", "available on godaddy", "get this domain",
    "buy this domain", "this domain is for sale", "domain is for sale",
    "domain for sale", "is available for sale", "purchase this domain",
    "make an offer on this domain", "make an offer", "sedo.com", "sedoparking",
    "dan.com", "afternic", "afternic.com", "hugedomains", "atom.com",
    "parkingcrew", "parked domain", "parked free", "domain expired",
    "site under construction", "domain may be for sale", "domain name is for sale",
]

_BLOCKED_WAF_MARKERS = [
    "403 forbidden", "you have been blocked", "access denied", "just a moment",
    "why have been blocked", "cloudflare", "checking your browser",
    "attention required", "error 403", "403 - forbidden", "forbidden nginx",
    "security check", "cf-challenge", "access to this page is denied",
]

_SERVER_ERROR_MARKERS = [
    "502 bad gateway", "503 service unavailable", "504 gateway time-out",
    "504 gateway timeout", "502 server error", "503 service temporarily unavailable",
    "bad gateway", "502 error", "error 502", "error 503", "error 504",
    "this site can't be reached", "this site cannot be reached", "dns_probe_finished",
    "err_name_not_resolved", "server ip address could not be found", "404 not found",
    "http error 404", "404 - not found", "page not found",
]


def build_filename_to_domain_map() -> dict[str, str]:
    """
    Build a fast lookup dictionary mapping screenshot filename -> domain ID
    from MongoDB checked_domains collection.
    """
    print("[+] Building filename-to-domain map from MongoDB...")
    fn_map = {}
    cursor = checked_domains().find({}, {"_id": 1, "domain": 1, "url": 1})
    for doc in cursor:
        dom_id = str(doc.get("_id"))
        dom = str(doc.get("domain") or dom_id)
        url = str(doc.get("url") or f"https://{dom}")

        candidates = all_filename_candidates(url, dom)
        for cand in candidates:
            fn_map[cand.lower()] = dom_id

        clean_dom = dom.removeprefix("www.")
        fn_map[_url_to_filename(clean_dom).lower()] = dom_id
        fn_map[_url_to_filename(dom).lower()] = dom_id

    print(f"[+] Mapped {len(fn_map):,} filename patterns.")
    return fn_map


def extract_domain_fallback(filename: str) -> str:
    """Fallback heuristic to derive domain from filename if not in MongoDB map."""
    stem = Path(filename).stem
    base = stem.rsplit("_", 1)[0] if "_" in stem else stem
    parts = base.rsplit("_", 1)
    if len(parts) == 2:
        return f"{parts[0]}.{parts[1]}".removeprefix("www.")
    return base.removeprefix("www.")


def classify_image_text(reader, img_path: Path) -> tuple[str | None, str]:
    """
    Use EasyOCR to inspect the text content of a screenshot.
    Returns (verdict, reason) where verdict is 'dead', 'blocked', or None (valid).
    """
    try:
        # Crop top 60% of image for 2x faster OCR execution (banners / titles live in top half)
        with Image.open(img_path) as img:
            w, h = img.size
            cropped = img.crop((0, 0, w, int(h * 0.65)))
            import io
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG")
            img_bytes = buf.getvalue()

        extracted_text = " ".join(reader.readtext(img_bytes, detail=0)).lower()
    except Exception:
        return None, ""

    if any(m in extracted_text for m in _PARKED_OR_FOR_SALE_MARKERS):
        return "dead", "Dead: Parked or For-Sale lander (GoDaddy/Sedo/Dan)"
    if any(m in extracted_text for m in _SERVER_ERROR_MARKERS):
        return "dead", "Dead: 502/503/504 Server or DNS error"
    if any(m in extracted_text for m in _BLOCKED_WAF_MARKERS):
        return "blocked", "Blocked: Cloudflare WAF / 403 Forbidden"

    return None, ""


def move_and_clean_screenshots(
    screenshots_dir: str = None,
    target_dir: str = None,
    use_ocr: bool = True
):
    get_db()

    if screenshots_dir is None:
        screenshots_dir = os.getenv("SCREENSHOT_DIR", str(PROJECT_ROOT / "output" / "screenshots"))
    if target_dir is None:
        target_dir = str(PROJECT_ROOT / "output" / "503error")

    src_path = Path(screenshots_dir)
    dst_path = Path(target_dir)

    if not src_path.exists():
        print(f"[!] Screenshot directory not found: {src_path}")
        return

    dst_path.mkdir(parents=True, exist_ok=True)

    # 1. Collect top-level screenshot files only
    image_files = [
        f for f in src_path.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
    ]

    total_images = len(image_files)
    print(f"\n[+] Total screenshots in {src_path}: {total_images:,}")
    print(f"[+] Quarantine destination : {dst_path}")
    print(f"[+] OCR text analysis      : {'Enabled' if use_ocr else 'Disabled'}")

    if total_images == 0:
        print("[!] No screenshots to process.")
        return

    reader = None
    if use_ocr:
        import easyocr
        print("[+] Initializing EasyOCR engine...")
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    # 2. Build filename -> domain map
    fn_map = build_filename_to_domain_map()

    # 3. Audit screenshots
    stats = {
        "moved_blank": 0,
        "moved_parked": 0,
        "moved_blocked": 0,
        "moved_server_err": 0,
        "valid_kept": 0,
    }
    checked_ops = []
    source_ops = []

    print("\n[+] Auditing screenshots (Blank check + Parked/GoDaddy OCR)...")
    for img_file in tqdm(image_files, desc="Auditing Screenshots", unit="img"):
        is_valid = is_valid_screenshot(str(img_file))
        verdict = None
        reason = ""

        if not is_valid:
            verdict = "dead"
            reason = "Dead: 503 / Blank unrendered screenshot"
            stats["moved_blank"] += 1
        elif use_ocr and reader:
            ocr_verdict, ocr_reason = classify_image_text(reader, img_file)
            if ocr_verdict:
                verdict = ocr_verdict
                reason = ocr_reason
                if "Parked" in reason:
                    stats["moved_parked"] += 1
                elif "Blocked" in reason:
                    stats["moved_blocked"] += 1
                else:
                    stats["moved_server_err"] += 1

        if verdict:
            filename = img_file.name
            target_file = dst_path / filename

            # Resolve domain ID
            domain_id = fn_map.get(filename.lower())
            if not domain_id:
                domain_id = extract_domain_fallback(filename)

            # Move file to quarantine folder
            try:
                if target_file.exists():
                    target_file.unlink()
                shutil.move(str(img_file), str(target_file))
            except Exception as e:
                tqdm.write(f"  [!] Failed to move {filename}: {e}")
                continue

            # Queue MongoDB updates
            if domain_id:
                checked_ops.append(
                    UpdateOne(
                        {"_id": domain_id},
                        {
                            "$set": {
                                "status": verdict,
                                "reason": reason,
                            },
                            "$unset": {
                                "screenshot_taken": "",
                                "screenshot_date": "",
                                "screenshot_failed_reason": "",
                                "exported": "",
                                "exported_at": "",
                            }
                        }
                    )
                )

                source_ops.append(
                    UpdateOne(
                        {"_id": domain_id},
                        {
                            "$set": {"domain": domain_id, "active": False, "processed": True},
                            "$unset": {"block_reason": ""}
                        }
                    )
                )

                # Batch flush every 500 ops
                if len(checked_ops) >= 500:
                    checked_domains().bulk_write(checked_ops, ordered=False)
                    source_domains().bulk_write(source_ops, ordered=False)
                    checked_ops.clear()
                    source_ops.clear()
        else:
            stats["valid_kept"] += 1

    # Final batch flush
    if checked_ops:
        checked_domains().bulk_write(checked_ops, ordered=False)
        source_domains().bulk_write(source_ops, ordered=False)

    total_moved = stats["moved_blank"] + stats["moved_parked"] + stats["moved_blocked"] + stats["moved_server_err"]

    # 4. Print Detailed Summary
    print("\n" + "=" * 65)
    print("      SCREENSHOT AUDIT & QUARANTINE CLEANUP SUMMARY")
    print("=" * 65)
    print(f"  Total Screenshots Audited      : {total_images:,}")
    print(f"  Total Moved to 503error Folder : {total_moved:,}")
    print(f"    ├─ Blank / Solid White       : {stats['moved_blank']:,}")
    print(f"    ├─ Parked / GoDaddy Landers  : {stats['moved_parked']:,}")
    print(f"    ├─ 502/503/504 Server Errors : {stats['moved_server_err']:,}")
    print(f"    └─ 403 / Cloudflare WAF      : {stats['moved_blocked']:,}")
    print(f"  Valid Gambling Kept on Disk    : {stats['valid_kept']:,}")
    print(f"  Quarantine Destination         : {dst_path}")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Move blank, 503, and parked domain screenshots to quarantine")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip OCR analysis (run blank check only)")
    args = parser.parse_args()

    move_and_clean_screenshots(use_ocr=not args.skip_ocr)


if __name__ == "__main__":
    main()
