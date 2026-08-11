"""
import_output_excel.py — High-performance importer for output.xlsx into MongoDB.

Imports domains into both collections:
1. domain_Listed (active: True / False / "blocked")
2. checked_domains (status: "gambling" / "regular" / "dead" / "blocked")
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

EXCEL_PATH = Path(__file__).resolve().parent / "output.xlsx"
BATCH_SIZE = 5000


def get_db():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB_NAME", "gamblingsites")
    client = MongoClient(uri)
    return client[db_name]


def clean_domain(raw_domain: str, raw_url: str) -> tuple[str, str]:
    dom = str(raw_domain or "").strip().lower()
    url = str(raw_url or "").strip()

    if not dom and url:
        dom = url.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")

    if dom.startswith("www."):
        dom = dom[4:]

    if not url:
        url = f"https://{dom}"
    elif not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    return dom, url


def import_excel():
    if not EXCEL_PATH.exists():
        print(f"[Error] Excel file not found at: {EXCEL_PATH}")
        return

    db = get_db()
    source_col = db[os.getenv("MONGO_COLLECTION", "domain_Listed")]
    checked_col = db[os.getenv("CHECKED_COLLECTION", "checked_domains")]

    # Create indexes for speed
    source_col.create_index("domain", unique=True)
    checked_col.create_index("status")

    print(f"Reading '{EXCEL_PATH.name}' sheets...")
    xl = pd.ExcelFile(EXCEL_PATH)

    sheet_mappings = [
        # (sheet_name, active_value, status_value)
        ("Verified", True, "gambling"),
        ("Rejected", True, "regular"),
        ("Dead", False, "dead"),
        ("Blocked", "blocked", "blocked"),
    ]

    now_iso = datetime.now(timezone.utc).isoformat()

    for sheet_name, active_val, status_val in sheet_mappings:
        if sheet_name not in xl.sheet_names:
            print(f"[Warning] Sheet '{sheet_name}' not found in Excel. Skipping...")
            continue

        print(f"\nProcessing sheet '{sheet_name}'...")
        df = xl.parse(sheet_name)
        total_rows = len(df)

        source_ops = []
        checked_ops = []
        count = 0

        pbar = tqdm(total=total_rows, desc=f"Importing {sheet_name}", unit="rows")

        for _, row in df.iterrows():
            dom, url = clean_domain(row.get("domain"), row.get("url"))
            if not dom:
                pbar.update(1)
                continue

            # Parse reasons/signals if present
            reasons_raw = row.get("matched_signals") or row.get("reason") or []
            if isinstance(reasons_raw, str):
                reasons = [r.strip() for r in reasons_raw.split(",") if r.strip()]
            elif isinstance(reasons_raw, list):
                reasons = reasons_raw
            else:
                reasons = []

            # checked_at
            chk_at = row.get("checked_at")
            checked_at_str = str(chk_at) if pd.notna(chk_at) else now_iso

            # 1. domain_Listed operation
            source_ops.append(
                UpdateOne(
                    {"domain": dom},
                    {"$setOnInsert": {"domain": dom, "active": active_val}},
                    upsert=True,
                )
            )

            # 2. checked_domains operation (strict 7-field schema)
            checked_doc = {
                "_id": dom,
                "domain": dom,
                "url": url,
                "status": status_val,
                "reason": reasons,
                "screenshot_taken": False,
                "screenshot_failed_reason": None,
            }
            checked_ops.append(
                UpdateOne(
                    {"_id": dom},
                    {"$set": checked_doc},
                    upsert=True,
                )
            )

            count += 1
            pbar.update(1)

            # Execute batch bulk write
            if len(source_ops) >= BATCH_SIZE:
                source_col.bulk_write(source_ops, ordered=False)
                checked_col.bulk_write(checked_ops, ordered=False)
                source_ops.clear()
                checked_ops.clear()

        # Write remaining batch
        if source_ops:
            source_col.bulk_write(source_ops, ordered=False)
            checked_col.bulk_write(checked_ops, ordered=False)
            source_ops.clear()
            checked_ops.clear()

        pbar.close()
        print(f"Completed '{sheet_name}': {count:,} domains imported/updated.")

    # Print summary counts
    print("\n" + "=" * 60)
    print("MongoDB Import Complete!")
    print(f"  domain_Listed total:   {source_col.count_documents({}):,}")
    print(f"  checked_domains total:  {checked_col.count_documents({}):,}")
    print(f"    - Gambling:           {checked_col.count_documents({'status': 'gambling'}):,}")
    print(f"    - Regular:            {checked_col.count_documents({'status': 'regular'}):,}")
    print(f"    - Dead:               {checked_col.count_documents({'status': 'dead'}):,}")
    print(f"    - Blocked:            {checked_col.count_documents({'status': 'blocked'}):,}")
    print("=" * 60)


if __name__ == "__main__":
    import_excel()
