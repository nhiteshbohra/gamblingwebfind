r"""
compare_dbs.py — Compare domain_Listed between two MongoDB databases and find new domains.

Usage:
    python project_sup/mongodbupdate/compare_dbs.py
"""
import os
from pathlib import Path
import pandas as pd
from pymongo import MongoClient

# Base MongoDB URI
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

# Database 1 (Base DB)
DB1_NAME = "gamblingsitetry"
COL1_NAME = "domain_Listed"

# Database 2 (Target DB with new crawl data)
DB2_NAME = "gamblingsites"
COL2_NAME = "domain_Listed"

OUTPUT_CSV = Path("output") / "new_domains_in_second_db.csv"


def _ask_yes_no(prompt: str) -> bool:
    """Prompt user for yes/no. Returns True for yes, False for no."""
    while True:
        answer = input(f"{prompt} [y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("    Please enter 'y' or 'n'.")


def compare_databases():
    print(f"[+] Connecting to MongoDB at: {MONGO_URI}")
    client = MongoClient(MONGO_URI)

    col1 = client[DB1_NAME][COL1_NAME]
    col2 = client[DB2_NAME][COL2_NAME]

    print(f"[+] Fetching domain IDs from DB 1: '{DB1_NAME}.{COL1_NAME}'...")
    set1 = set(doc["_id"] for doc in col1.find({}, {"_id": 1}))
    print(f"    -> DB 1 Total Unique Domains: {len(set1):,}")

    print(f"[+] Fetching domain IDs from DB 2: '{DB2_NAME}.{COL2_NAME}'...")
    set2 = set(doc["_id"] for doc in col2.find({}, {"_id": 1}))
    print(f"    -> DB 2 Total Unique Domains: {len(set2):,}")

    # Find domains in DB 2 that do NOT exist in DB 1
    new_in_db2_ids = set2 - set1
    print(f"\n[=] New Domains in '{DB2_NAME}' NOT present in '{DB1_NAME}': {len(new_in_db2_ids):,}")

    # Find domains in DB 1 that do NOT exist in DB 2
    new_in_db1_ids = set1 - set2
    print(f"[=] New Domains in '{DB1_NAME}' NOT present in '{DB2_NAME}': {len(new_in_db1_ids):,}")

    if new_in_db2_ids:
        print(f"\n[+] Extracting full documents for {len(new_in_db2_ids):,} new domains from DB 2...")
        docs = list(col2.find({"_id": {"$in": list(new_in_db2_ids)}}))

        OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(docs)
        df.to_csv(OUTPUT_CSV, index=False)
        print(f"[OK] Saved {len(docs):,} new domains to: {OUTPUT_CSV.resolve()}")

        # ── Ask user before importing ──────────────────────────────────────────
        print(f"\n[?] Found {len(docs):,} new domain(s) in '{DB2_NAME}' not in '{DB1_NAME}'.")
        if _ask_yes_no(f"    Do you want to import them into '{DB1_NAME}.{COL1_NAME}'?"):
            print(f"[+] Importing {len(docs):,} domains into '{DB1_NAME}.{COL1_NAME}'...")
            inserted = 0
            skipped  = 0
            for doc in docs:
                try:
                    col1.insert_one(doc)
                    inserted += 1
                except Exception:
                    # Duplicate key or other error — skip silently
                    skipped += 1
            print(f"[OK] Import complete. Inserted: {inserted:,}  |  Skipped (duplicates): {skipped:,}")
        else:
            print("[i] Import skipped. Domains saved to CSV only.")
        # ──────────────────────────────────────────────────────────────────────

    else:
        print(f"\n[i] No new domains found in '{DB2_NAME}' that aren't already in '{DB1_NAME}'.")

    if new_in_db1_ids:
        print(f"\n[+] Sample of domains in '{DB1_NAME}' not in '{DB2_NAME}' (first 10):")
        for d in list(new_in_db1_ids)[:10]:
            print(f"    - {d}")


if __name__ == "__main__":
    compare_databases()
