"""
import_output_excel.py — High-performance importer for Excel (.xlsx) files into MongoDB.

Imports domains into both collections:
1. domain_Listed (active: True / False / "blocked")
2. checked_domains (status: "gambling" / "regular" / "dead" / "blocked")
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

# Load .env (checks script dir, parent dirs, or root)
env_path = Path(__file__).resolve().parent / ".env"
if not env_path.exists():
    env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

BATCH_SIZE = 5000


def get_db():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB_NAME", "gamblingsites")
    client = MongoClient(uri)
    return client[db_name]


def get_excel_path() -> Path:
    """Prompt user interactively for Excel file path or use CLI argument/default."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        cli_path = Path(sys.argv[1].strip().strip('"').strip("'"))
        if cli_path.exists():
            return cli_path
        print(f"[Warning] Provided path '{cli_path}' does not exist.")

    possible_paths = [
        Path(__file__).resolve().parent.parent / "first_slot_data/output.xlsx",
        Path(__file__).resolve().parent / "first_slot_data/output.xlsx",
        Path(__file__).resolve().parents[2] / "first_slot_data/output.xlsx",
    ]
    default_path = next((p for p in possible_paths if p.exists()), None)
    default_str = str(default_path) if default_path else ""

    prompt_str = "Enter the path to the Excel (.xlsx) file"
    if default_str:
        prompt_str += f" [Press Enter for default: {default_str}]"
    prompt_str += ": "

    user_input = input(prompt_str).strip().strip('"').strip("'")

    if not user_input:
        if default_path and default_path.exists():
            return default_path
        print("[Error] No path provided and default Excel file was not found.")
        sys.exit(1)

    chosen_path = Path(user_input)
    if not chosen_path.exists():
        print(f"[Error] Excel file not found at: {chosen_path}")
        sys.exit(1)

    return chosen_path


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


def import_excel(excel_path: Path = None):
    if excel_path is None:
        excel_path = get_excel_path()

    if not excel_path.exists():
        print(f"[Error] Excel file not found at: {excel_path}")
        sys.exit(1)

    db = get_db()
    source_col = db[os.getenv("MONGO_COLLECTION", "domain_Listed")]
    checked_col = db[os.getenv("CHECKED_COLLECTION", "checked_domains")]

    # Create indexes for speed
    source_col.create_index("domain", unique=True)

    print(f"\nReading '{excel_path.name}' ({excel_path})...")
    xl = pd.ExcelFile(excel_path)

    sheet_mappings = [
        # (sheet_name, active_value, status_value)
        ("Verified", True, "gambling"),
        ("Rejected", True, "regular"),
        ("Dead", False, "dead"),
        ("Blocked", "blocked", "blocked"),
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

            # 1. domain_Listed operation (_id is domain string, matching checked_domains)
            source_ops.append(
                UpdateOne(
                    {"_id": dom},
                    {
                        "$set": {"_id": dom, "domain": dom, "active": active_val},
                        "$setOnInsert": {"added_date": today_date},
                    },
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
                    {
                        "$set": checked_doc,
                        "$setOnInsert": {"added_date": today_date},
                    },
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
