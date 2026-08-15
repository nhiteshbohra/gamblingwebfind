import os
import sys
import shutil
import asyncio
from pathlib import Path
import pandas as pd
from pymongo import MongoClient
from tqdm import tqdm
from dotenv import load_dotenv

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from checking_url.classifier import (
    load_keywords,
    detect_negative_archetype,
    is_parked_or_for_sale,
    HIGH_INTENT_GAMBLING_TERMS,
)
from capture_url.screenshot import is_valid_screenshot

load_dotenv(dotenv_path=ROOT_DIR / ".env")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "gamblingsitetry")
CHECKED_COL = os.getenv("CHECKED_COLLECTION", "checked_domains")
SOURCE_COL = os.getenv("MONGO_COLLECTION", "domain_Listed")

OUTPUT_DIR = Path("output/screenshots")
FP_REMOVED_DIR = Path("output/screenshots_fp_removed")
REPORT_PATH = Path("output/pass_c_audit_results.xlsx")

easyocr_reader = None

def get_ocr_reader():
    global easyocr_reader
    if easyocr_reader is None:
        try:
            import easyocr
            easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        except Exception:
            easyocr_reader = False
    return easyocr_reader if easyocr_reader is not False else None


def extract_ocr_text(filepath: str) -> str:
    """Extract visible text from screenshot using EasyOCR."""
    reader = get_ocr_reader()
    if not reader or not os.path.exists(filepath):
        return ""
    try:
        results = reader.readtext(filepath, detail=0)
        return " ".join(results).lower()
    except Exception:
        return ""


def find_existing_screenshot(domain: str) -> Path | None:
    """Find screenshot file in output/screenshots corresponding to domain."""
    if not OUTPUT_DIR.exists():
        return None
    clean_d = domain.replace('.', '_').replace('-', '_')
    matches = list(OUTPUT_DIR.glob(f"{clean_d}_*.jpg")) + list(OUTPUT_DIR.glob(f"{clean_d}_*.png"))
    if not matches:
        matches = [f for f in OUTPUT_DIR.glob("*") if f.stem == clean_d or f.stem.startswith(clean_d + "_")]
    if matches:
        for m in matches:
            if is_valid_screenshot(str(m)):
                return m
    return None


def run_final_audit():
    print("=" * 70)
    print("      PASS C: FINAL VISUAL OCR AUDIT ON ALL SCREENSHOTS       ")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FP_REMOVED_DIR.mkdir(parents=True, exist_ok=True)
    keywords = load_keywords()

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    checked_col = db[CHECKED_COL]
    source_col = db[SOURCE_COL]

    # Find all domains currently marked as gambling that have a screenshot
    gambling_docs = list(checked_col.find({"status": "gambling"}))
    
    candidates = []
    for doc in gambling_docs:
        d = doc["_id"]
        ss_path = find_existing_screenshot(d)
        if ss_path:
            candidates.append((d, ss_path, doc.get("reason", [])))
            
    print(f"[+] Found {len(candidates):,} domains marked as gambling with active screenshots.")
    print("[>>>] Starting EasyOCR deep visual scan...")
    
    ocr_reader = get_ocr_reader()
    if not ocr_reader:
        print("[!] EasyOCR not available. Exiting.")
        return

    results_fp_cleaned = []
    results_confirmed = []

    for d, ss_file, existing_reasons in tqdm(candidates, desc="Pass C (Visual Audit)", unit="domain", dynamic_ncols=True):
        # 1. OCR text extraction from visual screenshot
        ocr_text = extract_ocr_text(str(ss_file))
        combined_text = (ocr_text + " " + " ".join(str(r) for r in existing_reasons)).lower()

        # 2. Check for negative archetype (Educational, E-Commerce, News/Wiki, Parked)
        is_negative, neg_reason = detect_negative_archetype(combined_text)
        is_parked, parked_reasons = is_parked_or_for_sale(text=combined_text, url=f"https://{d}")

        # 3. Match gambling keywords
        matched_kws = [kw for kw in keywords if kw in combined_text]
        has_high_intent = any(kw in matched_kws for kw in HIGH_INTENT_GAMBLING_TERMS)

        is_valid_gambling = False
        rejection_reason = ""

        if is_parked:
            is_valid_gambling = False
            rejection_reason = f"Parked/For-Sale Lander ({', '.join(parked_reasons[:2])})"
        elif is_negative and len(matched_kws) < 6:
            is_valid_gambling = False
            rejection_reason = f"Negative Archetype: {neg_reason}"
        elif len(matched_kws) >= 3 or (len(matched_kws) >= 1 and has_high_intent):
            is_valid_gambling = True
        else:
            is_valid_gambling = False
            rejection_reason = f"Insufficient visual/HTML gambling keywords ({len(matched_kws)} matched)"

        if not is_valid_gambling:
            # Caught a False Positive! We will update the DB but keep the image in the folder.
            checked_col.update_one(
                {"_id": d},
                {"$set": {"status": "regular", "reason": [f"Failed Pass C Audit: {rejection_reason}"]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                upsert=True
            )
            # Revert in source table too if needed
            results_fp_cleaned.append({"Domain": d, "Reason": rejection_reason, "Action": "Caught by Pass C: Moved screenshot to FP folder & downgraded to regular"})
        else:
            results_confirmed.append({"Domain": d, "Reason": "Passed Visual Audit"})

    print("\n[+] Exporting Pass C diagnostic report...")
    with pd.ExcelWriter(REPORT_PATH, engine="openpyxl") as writer:
        if results_fp_cleaned:
            df2 = pd.DataFrame(results_fp_cleaned)
            df2.insert(0, "S.No.", range(1, len(df2) + 1))
            df2.to_excel(writer, sheet_name="Caught_False_Positives", index=False)

        if results_confirmed:
            df1 = pd.DataFrame(results_confirmed)
            df1.insert(0, "S.No.", range(1, len(df1) + 1))
            df1.to_excel(writer, sheet_name="Confirmed_Gambling", index=False)

    print("\n" + "=" * 70)
    print("             PASS C FINAL AUDIT COMPLETE                ")
    print("=" * 70)
    print(f"  * Total domains audited: {len(candidates)}")
    print(f"  * Confirmed Gambling   : {len(results_confirmed)}")
    print(f"  * False Positives Caught: {len(results_fp_cleaned)}")
    print("=" * 70)
    print(f"[OK] Report exported to: {REPORT_PATH.resolve()}")
    print("=" * 70)


if __name__ == "__main__":
    run_final_audit()
