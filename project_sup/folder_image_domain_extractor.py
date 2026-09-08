"""
project_sup/folder_image_domain_extractor.py — Extract Domains from Image Folder & Export CSV
================================================================================================

Prompts the user for a folder path containing screenshot images (.png, .jpg, .jpeg, .webp),
extracts domain names from the image filenames, deduplicates them, and exports a clean CSV.

Usage:
    python -m project_sup.folder_image_domain_extractor
    python -m project_sup.folder_image_domain_extractor --folder "output/screenshots"
    python -m project_sup.folder_image_domain_extractor --folder "output/screenshots" --output "output/folder_domains.csv"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

import pandas as pd
from tqdm import tqdm

IST = timezone(timedelta(hours=5, minutes=30))
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
DEFAULT_SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


def clean_path_input(raw: str) -> str:
    """Clean a folder path typed or pasted from Windows Explorer / PowerShell."""
    cleaned = raw.strip()
    if cleaned.startswith("& "):
        cleaned = cleaned[2:].strip()
    return cleaned.strip('"').strip("'")


def extract_domain_from_filename(filename: str) -> str:
    """
    Extract a clean domain name from an image filename.
    Handles formats like:
      - spinquest.com.png -> spinquest.com
      - https___spinquest_com_a1b2c3d4.jpg -> spinquest.com
      - spinquest_com_f8a912b3.png -> spinquest.com
      - www_spinquest_com.jpeg -> spinquest.com
    """
    p = Path(filename)
    stem = p.stem  # Filename without extension

    # 1. Remove scheme/subdomain prefixes
    clean = stem
    for prefix in ("https___", "http___", "https_", "http_", "www_"):
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):]

    # 2. Strip trailing 8-character hex hash if present (e.g., _a1b2c3d4)
    clean = re.sub(r'_[a-f0-9]{8}$', '', clean, flags=re.IGNORECASE)

    # 3. Strip path components appended to filenames (e.g. domain_path_hash)
    # If dots exist (spinquest.com), use direct dot logic
    if "." in clean:
        parts = clean.split(".")
        if len(parts) >= 2:
            domain_cand = f"{parts[-2]}.{parts[-1]}"
            # Keep parent domain if valid (e.g. sub.spinquest.com)
            domain_cand = clean.removeprefix("www.")
            return domain_cand.lower()

    # 4. If underscores were used as dot replacements (spinquest_com)
    # Replace single or double underscores with dots or sanitize
    if "_" in clean:
        # Check if last token after underscore is TLD (com, net, org, in, etc.)
        tokens = clean.split("_")
        if len(tokens) >= 2:
            # Reconstruct domain with dot
            clean_dom = ".".join(tokens[:2]) if len(tokens) == 2 else ".".join(tokens)
            clean_dom = clean_dom.removeprefix("www.")
            return clean_dom.lower()

    return clean.removeprefix("www.").lower()


def process_folder(folder_path: str, output_path: str | None = None) -> dict:
    folder = Path(clean_path_input(folder_path))
    if not folder.is_dir():
        raise SystemExit(f"[!] Error: Target folder not found: '{folder}'")

    print(f"\n[extractor] Scanning images in folder: '{folder.resolve()}'...")

    image_files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]

    total_images = len(image_files)
    print(f"[extractor] Found {total_images:,} image file(s).")

    if total_images == 0:
        print("[extractor] No image files found in folder.")
        return {"total_images": 0, "unique_domains": 0}

    records = []
    seen_domains = set()

    for p in tqdm(image_files, desc="Extracting Domains", unit="img"):
        raw_filename = p.name
        domain = extract_domain_from_filename(raw_filename)
        ext = p.suffix.lower()

        records.append({
            "Domain": domain,
            "Image_Filename": raw_filename,
            "File_Extension": ext,
            "File_Size_KB": round(p.stat().st_size / 1024, 1),
            "Full_Path": str(p.resolve()),
        })
        if domain:
            seen_domains.add(domain)

    unique_domains_count = len(seen_domains)

    # Export CSV
    now_str = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    folder_name_slug = folder.name or "folder"

    if not output_path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / f"extracted_domains_{folder_name_slug}_{now_str}.csv"
        unique_csv_path = out_dir / f"unique_domains_{folder_name_slug}_{now_str}.csv"
    else:
        out_p = Path(clean_path_input(output_path))
        out_p.parent.mkdir(parents=True, exist_ok=True)
        csv_path = out_p.with_suffix(".csv")
        unique_csv_path = out_p.parent / f"unique_{out_p.stem}.csv"

    df_full = pd.DataFrame(records)
    df_full.to_csv(csv_path, index=False, encoding="utf-8-sig")

    df_unique = pd.DataFrame(sorted(seen_domains), columns=["Domain"])
    df_unique.to_csv(unique_csv_path, index=False, encoding="utf-8-sig")

    print(f"\n" + "=" * 65)
    print("           FOLDER DOMAIN EXTRACTION SUMMARY")
    print("=" * 65)
    print(f"  Target Folder         : {folder.resolve()}")
    print(f"  Total Images Found    : {total_images:,}")
    print(f"  Unique Domains Found  : {unique_domains_count:,}")
    print(f"")
    print(f"  Full Image Details CSV : {csv_path.resolve()}")
    print(f"  Unique Domains CSV     : {unique_csv_path.resolve()}")
    print("=" * 65 + "\n")

    return {
        "total_images": total_images,
        "unique_domains": unique_domains_count,
        "full_csv": str(csv_path),
        "unique_csv": str(unique_csv_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Prompt for image folder, extract domain names from screenshot filenames, and export CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--folder", "-f", default=None, help="Path to folder containing screenshot images")
    parser.add_argument("--output", "-o", default=None, help="Path for output CSV report")
    args = parser.parse_args()

    folder = args.folder
    if not folder:
        default_dir = str(PROJECT_ROOT / DEFAULT_SCREENSHOT_DIR)
        print("\n--- Image Folder Domain Extractor ---")
        prompt_text = f"Enter folder path containing screenshot images [default: {default_dir}]: "
        try:
            typed = clean_path_input(input(prompt_text))
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return
        folder = typed or default_dir

    process_folder(folder_path=folder, output_path=args.output)


if __name__ == "__main__":
    main()

