r"""
project_sup/visual_check.py — Visual Screenshot Audit & Folder Sorter
======================================================================

Visually audits all screenshot images in a user-specified folder using OCR
(Tesseract) and Vision AI (Ollama) and sorts them directly on disk into:
  • <folder>/gambling/  -- Confirmed online gambling operator sites
  • <folder>/false/     -- Regular websites, dead pages, and institutional
                           exceptions (hotels, resorts, banks, schools)

Pure filesystem organization:
- Does NOT write, update, or alter anything in MongoDB.
- Protects the live permanent archive (output/screenshots/).

Usage:
  python -m project_sup.visual_check
  python -m project_sup.visual_check --input "output/screenshots/New folder - Copy"
  python -m project_sup.visual_check --input ./batch1 --dry-run
  python project_sup/visual_check.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

# ── Ensure project root is on sys.path ───────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from tqdm import tqdm

from export_domains.screenshot import is_valid_screenshot
from checking_url.ocr_extractor import extract_ocr_text
from checking_url.ai_classifier import (
    classify_screenshot_for_sorting,
    close_ai_session,
    start_ollama_if_needed,
)
from checking_url.classifier import (
    load_keywords,
    is_hospitality_site,
    detect_negative_archetype,
)

PROTECTED_ARCHIVE = PROJECT_ROOT / "output" / "screenshots"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
INSTITUTIONAL_ARCHETYPES = {"educational", "commercial_banking"}


def clean_path_input(raw: str) -> str:
    """Clean a path typed or pasted from Windows Explorer / PowerShell."""
    cleaned = raw.strip()
    if cleaned.startswith("& "):
        cleaned = cleaned[2:].strip()
    return cleaned.strip('"').strip("'")


def _refuse_if_protected_archive(input_dir: Path):
    protected = PROTECTED_ARCHIVE.resolve()
    resolved = input_dir.resolve()
    live_archives = {protected, (protected / "New folder").resolve()}
    if resolved in live_archives or resolved in protected.parents:
        raise SystemExit(
            f"Refusing to run: '{resolved}' is (or contains) the protected archive "
            f"'{protected}'. Nothing may ever move a file out of the live archive -- "
            f"point this at a copy (e.g. 'New folder - Copy') or a different folder."
        )


def _guess_domain_from_filename(path: Path) -> str:
    """Best-effort label for vision model prompt from capture filename."""
    stem = path.stem
    parts = stem.rsplit("_", 1)
    return parts[0] if len(parts) == 2 and len(parts[1]) == 8 else stem


def decide_gambling_or_false(ocr_text: str, vision_res: dict | None, keywords: set) -> dict:
    """Combine OCR text + Vision AI result to determine gambling vs false.

    Institutional sites (hotels, resorts, schools, banks) are strictly classified
    as 'false' even if casino imagery is visible, to avoid false positives.
    """
    ocr_lower = (ocr_text or "").lower()
    is_hosp, _ = is_hospitality_site(ocr_lower)
    is_neg, neg_reason = detect_negative_archetype(ocr_lower)
    ocr_institutional = is_hosp or (is_neg and any(tag in (neg_reason or "") for tag in INSTITUTIONAL_ARCHETYPES))

    vision_institutional = bool((vision_res or {}).get("is_institutional"))
    is_institutional = ocr_institutional or vision_institutional

    vision_gambling = bool((vision_res or {}).get("is_gambling"))
    ocr_hit = next((kw for kw in keywords if kw in ocr_lower), None)
    is_gambling = (vision_gambling or bool(ocr_hit)) and not is_institutional

    evidence_bits = []
    if is_institutional:
        source = "OCR text" if ocr_institutional else "vision model"
        evidence_bits.append(f"institutional override ({source}) -- hotel/school/bank, not an operator")
    elif vision_gambling:
        evidence_bits.append(f"vision: {(vision_res or {}).get('visual_evidence', 'gambling UI detected')}")
    if ocr_hit and not is_institutional:
        evidence_bits.append(f"ocr_keyword: {ocr_hit}")
    if not evidence_bits:
        evidence_bits.append("neither OCR text nor vision model found gambling evidence in image")

    return {
        "is_gambling": is_gambling,
        "is_institutional": is_institutional,
        "evidence": "; ".join(evidence_bits),
    }


async def run(
    input_dir: str,
    output_dir: str = None,
    recursive: bool = False,
    dry_run: bool = False,
    limit: int = 0,
) -> dict:
    in_path = Path(clean_path_input(str(input_dir)))
    if not in_path.is_dir():
        raise SystemExit(f"Input folder not found: {input_dir}")
    _refuse_if_protected_archive(in_path)

    try:
        await start_ollama_if_needed()
    except Exception as e:
        print(f"[!] Warning: Local AI check failed: {e}")

    out_path = Path(clean_path_input(str(output_dir))) if output_dir else in_path
    gambling_dir = out_path / "gambling"
    false_dir = out_path / "false"

    pattern = "**/*" if recursive else "*"
    images = [
        p for p in in_path.glob(pattern)
        if p.suffix.lower() in IMAGE_EXTENSIONS
        and gambling_dir not in p.parents and false_dir not in p.parents
        and is_valid_screenshot(str(p))
    ]

    print(f"\n[visual_check] Found {len(images)} valid screenshot image(s) in {in_path}"
          f"{' (DRY RUN - files will not move)' if dry_run else ''}.")

    if not images:
        return {"checked": 0, "gambling": 0, "false": 0}

    if limit > 0:
        images = images[:limit]
        print(f"[visual_check] Limiting to first {limit} screenshot(s).")

    keywords = load_keywords()
    stats = {"checked": 0, "gambling": 0, "false": 0}

    try:
        for img_path in tqdm(images, desc="Visual Auditing", unit="img"):
            label = _guess_domain_from_filename(img_path)
            ocr_text = await asyncio.to_thread(extract_ocr_text, str(img_path))
            vision_res = await classify_screenshot_for_sorting(str(img_path), label=label)
            result = decide_gambling_or_false(ocr_text, vision_res, keywords)
            stats["checked"] += 1

            bucket = "gambling" if result["is_gambling"] else "false"
            stats[bucket] += 1
            tag = "GAMBLING" if result["is_gambling"] else "FALSE"

            tqdm.write(f"  [{tag}] {img_path.name} -- {result['evidence'][:90]}")

            if not dry_run:
                dest_dir = gambling_dir if result["is_gambling"] else false_dir
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(img_path), str(dest_dir / img_path.name))

    finally:
        await close_ai_session()

    print("\n" + "=" * 65)
    print("         VISUAL SCREENSHOT AUDIT SUMMARY" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 65)
    print(f"  Total Visually Audited  : {stats['checked']:,}")
    print(f"  -> gambling/            : {stats['gambling']:,}  ({gambling_dir})")
    print(f"  -> false/               : {stats['false']:,}  ({false_dir})")
    print("=" * 65 + "\n")
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Visually audit a folder of screenshots (OCR + Vision AI) and sort into gambling/ vs false/ folders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m project_sup.visual_check --input "output/screenshots/New folder - Copy"
  python -m project_sup.visual_check --input ./batch1 --dry-run
  python -m project_sup.visual_check --input ./batch1 --limit 50
""",
    )
    parser.add_argument("--input", default=None, help="Folder of screenshots to visually audit (prompts if omitted)")
    parser.add_argument("--output", default=None, help="Folder to create gambling/ and false/ subfolders in (default: same as --input)")
    parser.add_argument("--recursive", action="store_true", help="Also scan subfolders of --input")
    parser.add_argument("--dry-run", action="store_true", help="Preview classification and moves without touching files")
    parser.add_argument("--limit", type=int, default=0, help="Max screenshots to audit (0 = all)")
    args = parser.parse_args()

    input_dir = args.input
    if not input_dir:
        default_dir = str(PROJECT_ROOT / "output" / "screenshots" / "New folder - Copy")
        print("\n--- Visual Screenshot Audit (OCR + Vision AI) ---")
        typed = clean_path_input(input(f"Enter screenshots folder to audit [default: {default_dir}]: "))
        input_dir = typed or default_dir

    asyncio.run(
        run(
            input_dir=input_dir,
            output_dir=args.output,
            recursive=args.recursive,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
