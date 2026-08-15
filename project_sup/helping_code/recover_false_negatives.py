r"""
project_sup/helping_code/recover_false_negatives.py — Generalized High-Accuracy Gambling Recovery & FP Cleaning Suite.

Zero hard-coded domain names:
- Uses generalized negative archetype detection (educational, e-commerce, news/media, parked landers).
- Uses multi-keyword & high-intent cluster verification.
- Pass A: Fast-Pass audit of existing screenshots (~700 domains) using OCR + semantic analysis.
- Pass B: Browser crawl for missing screenshots (~3,000 domains) with strict classification.

Exports full diagnostic report to: output/recovery_and_fp_audit_results.xlsx
"""
import os
import sys
import asyncio
import shutil
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient
from tqdm import tqdm

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from capture_url.screenshot import BrowserPool, is_valid_screenshot
from checking_url.classifier import (
    classify,
    load_keywords,
    detect_negative_archetype,
    is_parked_or_for_sale,
    HIGH_INTENT_GAMBLING_TERMS,
)

load_dotenv(dotenv_path=ROOT_DIR / ".env")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "gamblingsitetry")
CHECKED_COL = os.getenv("CHECKED_COLLECTION", "checked_domains")
SOURCE_COL = os.getenv("MONGO_COLLECTION", "domain_Listed")

OUTPUT_DIR = Path("output/screenshots")
FP_REMOVED_DIR = Path("output/screenshots_fp_removed")
REPORT_PATH = Path("output/recovery_and_fp_audit_results.xlsx")

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


async def run_recovery():
    print("=" * 70)
    print("   GENERALIZED HIGH-ACCURACY GAMBLING RECOVERY & FP CLEANER   ")
    print("=" * 70)

    excel_path = Path("output/status_changes_compare.xlsx")
    if not excel_path.exists():
        print(f"[!] Error: {excel_path} not found. Please generate it first.")
        return

    df_excel = pd.read_excel(excel_path)
    domains = [str(d).strip().lower() for d in df_excel["Domain"].dropna().unique() if str(d).strip()]
    print(f"[+] Loaded {len(domains):,} candidate domains from {excel_path.name}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FP_REMOVED_DIR.mkdir(parents=True, exist_ok=True)
    keywords = load_keywords()

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    checked_col = db[CHECKED_COL]
    source_col = db[SOURCE_COL]

    pass_a_domains = []
    pass_b_domains = []
    domain_screenshot_map = {}

    for d in domains:
        ss_path = find_existing_screenshot(d)
        if ss_path:
            pass_a_domains.append(d)
            domain_screenshot_map[d] = ss_path
        else:
            pass_b_domains.append(d)

    print(f"\n[+] Partition Summary:")
    print(f"  * PASS A (Existing Screenshots to Audit) : {len(pass_a_domains):,}")
    print(f"  * PASS B (Missing Screenshots to Capture): {len(pass_b_domains):,}")
    print("=" * 70)

    results_recovered = []
    results_fp_cleaned = []
    results_regular = []
    results_blocked_dead = []

    # ── PASS A: Fast-Pass Generalized Audit on Existing Screenshots ───────────
    if pass_a_domains:
        print("\n[>>>] PASS A: Auditing Existing Screenshots with Semantic Archetype & OCR Analysis...")
        ocr_reader = get_ocr_reader()

        for d in tqdm(pass_a_domains, desc="Pass A (Audit Screenshots)", unit="domain", dynamic_ncols=True):
            ss_file = domain_screenshot_map[d]
            doc = checked_col.find_one({"_id": d})
            existing_reasons = doc.get("reason", []) if doc else []

            # 1. OCR text extraction from visual screenshot
            ocr_text = extract_ocr_text(str(ss_file)) if ocr_reader else ""
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

            if is_valid_gambling:
                # Confirmed Gambling
                checked_col.update_one(
                    {"_id": d},
                    {"$set": {"status": "gambling", "screenshot_taken": True, "screenshot_failed_reason": None, "reason": matched_kws or existing_reasons}},
                    upsert=True
                )
                source_col.update_one({"_id": d}, {"$set": {"active": True, "processed": True}})
                results_recovered.append({"Domain": d, "Source": "Pass A (Existing Screenshot)", "Reason": ", ".join(matched_kws[:5])})
            else:
                # False Positive
                dest_path = FP_REMOVED_DIR / ss_file.name
                shutil.move(str(ss_file), str(dest_path))
                checked_col.update_one(
                    {"_id": d},
                    {"$set": {"status": "regular", "reason": [rejection_reason]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                    upsert=True
                )
                source_col.update_one({"_id": d}, {"$set": {"active": True, "processed": True}})
                results_fp_cleaned.append({"Domain": d, "Reason": rejection_reason, "Action": "Moved screenshot to FP folder & updated status to regular"})

    # ── PASS B: Playwright Capture & Classify Missing Screenshots ─────────────
    if pass_b_domains:
        print("\n[>>>] PASS B: Capturing & Classifying Missing Domains via Browser...")
        pool = BrowserPool(concurrency=10)
        await pool.start()

        sem = asyncio.Semaphore(10)
        pbar = tqdm(total=len(pass_b_domains), desc="Pass B (Browser Capture)", unit="domain", dynamic_ncols=True)

        async def process_domain_pass_b(domain: str):
            async with sem:
                try:
                    url = f"https://{domain}"
                    filepath, status, reason = await pool.capture_url(
                        url, str(OUTPUT_DIR), retries=2, keywords=keywords
                    )

                    if status == "success" and filepath:
                        checked_col.update_one(
                            {"_id": domain},
                            {"$set": {
                                "status": "gambling",
                                "screenshot_taken": True,
                                "screenshot_failed_reason": None,
                                "reason": reason if isinstance(reason, list) else [str(reason)],
                            }},
                            upsert=True
                        )
                        source_col.update_one({"_id": domain}, {"$set": {"active": True, "processed": True}})
                        results_recovered.append({"Domain": domain, "Source": "Pass B (New Capture)", "Reason": ", ".join(reason) if isinstance(reason, list) else str(reason)})
                    elif status == "regular":
                        checked_col.update_one(
                            {"_id": domain},
                            {"$set": {"status": "regular", "reason": reason if isinstance(reason, list) else [str(reason)]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                            upsert=True
                        )
                        source_col.update_one({"_id": domain}, {"$set": {"active": True, "processed": True}})
                        results_regular.append({"Domain": domain, "Reason": ", ".join(reason) if isinstance(reason, list) else str(reason)})
                    elif status == "blocked":
                        checked_col.update_one(
                            {"_id": domain},
                            {"$set": {"status": "blocked", "reason": [str(reason)]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                            upsert=True
                        )
                        source_col.update_one({"_id": domain}, {"$set": {"active": "blocked", "processed": True}})
                        results_blocked_dead.append({"Domain": domain, "Status": "blocked", "Reason": str(reason)})
                    else:  # dead
                        checked_col.update_one(
                            {"_id": domain},
                            {"$set": {"status": "dead", "reason": [str(reason)]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                            upsert=True
                        )
                        source_col.update_one({"_id": domain}, {"$set": {"active": False, "processed": True}})
                        results_blocked_dead.append({"Domain": domain, "Status": "dead", "Reason": str(reason)})
                except Exception as err:
                    checked_col.update_one(
                        {"_id": domain},
                        {"$set": {"status": "dead", "reason": [f"Exception: {str(err)[:60]}"]}, "$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}},
                        upsert=True
                    )
                    results_blocked_dead.append({"Domain": domain, "Status": "dead", "Reason": f"Exception: {str(err)[:60]}"})
                finally:
                    pbar.update(1)

        await asyncio.gather(*[process_domain_pass_b(d) for d in pass_b_domains], return_exceptions=True)
        pbar.close()
        await pool.close()

    # ── Export Results to Excel ───────────────────────────────────────────────
    print("\n[+] Exporting diagnostic Excel report...")
    with pd.ExcelWriter(REPORT_PATH, engine="openpyxl") as writer:
        if results_recovered:
            df1 = pd.DataFrame(results_recovered)
            df1.insert(0, "S.No.", range(1, len(df1) + 1))
            df1.to_excel(writer, sheet_name="Recovered_Gambling", index=False)

        if results_fp_cleaned:
            df2 = pd.DataFrame(results_fp_cleaned)
            df2.insert(0, "S.No.", range(1, len(df2) + 1))
            df2.to_excel(writer, sheet_name="Cleaned_False_Positives", index=False)

        if results_regular:
            df3 = pd.DataFrame(results_regular)
            df3.insert(0, "S.No.", range(1, len(df3) + 1))
            df3.to_excel(writer, sheet_name="Confirmed_Regular", index=False)

        if results_blocked_dead:
            df4 = pd.DataFrame(results_blocked_dead)
            df4.insert(0, "S.No.", range(1, len(df4) + 1))
            df4.to_excel(writer, sheet_name="Blocked_and_Dead", index=False)

        summary_data = [
            {"Metric": "Total Candidate Domains Processed", "Value": len(domains)},
            {"Metric": "Pass A: Existing Screenshots Audited", "Value": len(pass_a_domains)},
            {"Metric": "Pass B: Missing Screenshots Processed", "Value": len(pass_b_domains)},
            {"Metric": "Confirmed & Recovered Gambling Domains", "Value": len(results_recovered)},
            {"Metric": "False Positives Cleaned (Moved to FP folder)", "Value": len(results_fp_cleaned)},
            {"Metric": "Confirmed Regular Domains", "Value": len(results_regular)},
            {"Metric": "Blocked / Dead Domains", "Value": len(results_blocked_dead)},
        ]
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

    print("\n" + "=" * 70)
    print("             RECOVERY & FP CLEANUP COMPLETE SUMMARY             ")
    print("=" * 70)
    for s in summary_data:
        print(f"  * {s['Metric']:<45}: {s['Value']:,}")
    print("=" * 70)
    print(f"[OK] Full Diagnostic Report exported to: {REPORT_PATH.resolve()}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_recovery())
