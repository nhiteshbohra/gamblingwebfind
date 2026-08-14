r"""
compare_dbs.py — Compare domain_Listed between two MongoDB databases and find missing domains.

Usage:
    python project_sup/helping_code/compare_dbs.py
"""
import os
from pymongo import MongoClient

# Base MongoDB URI
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

# Database 1 (Base DB)
DB1_NAME = "gamblingsitetry"
COL1_NAME = "domain_Listed"

# Database 2 (Target DB with new crawl data)
DB2_NAME = "gamblingsites"
COL2_NAME = "domain_Listed"


def _ask_yes_no(prompt: str) -> bool:
    """Prompt user for yes/no. Returns True for yes, False for no."""
    while True:
        answer = input(f"{prompt} [y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("    Please enter 'y' or 'n'.")


def _fetch_and_import(src_col, target_col, id_set: set, src_label: str, target_label: str):
    """Fetch documents in chunks from src_col and insert missing ones into target_col."""
    id_list = list(id_set)
    chunk_size = 5000
    inserted = 0
    skipped = 0
    print(f"[+] Extracting and importing {len(id_list):,} domains into '{target_label}'...")

    for i in range(0, len(id_list), chunk_size):
        chunk_ids = id_list[i : i + chunk_size]
        docs = list(src_col.find({"_id": {"$in": chunk_ids}}))
        for doc in docs:
            try:
                target_col.insert_one(doc)
                inserted += 1
            except Exception:
                skipped += 1

    print(f"[OK] Import complete. Inserted: {inserted:,}  |  Skipped (duplicates): {skipped:,}")


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

    # 1) Option to sync DB 2 -> DB 1
    if new_in_db2_ids:
        print(f"\n[?] Found {len(new_in_db2_ids):,} domain(s) in '{DB2_NAME}' not in '{DB1_NAME}'.")
        if _ask_yes_no(f"    Do you want to add them into '{DB1_NAME}.{COL1_NAME}'?"):
            _fetch_and_import(col2, col1, new_in_db2_ids, f"{DB2_NAME}.{COL2_NAME}", f"{DB1_NAME}.{COL1_NAME}")
        else:
            print(f"[i] Skipped adding domains to '{DB1_NAME}'.")
    else:
        print(f"\n[i] No new domains found in '{DB2_NAME}' that aren't already in '{DB1_NAME}'.")

    # 2) Option to sync DB 1 -> DB 2
    if new_in_db1_ids:
        print(f"\n[?] Found {len(new_in_db1_ids):,} domain(s) in '{DB1_NAME}' not in '{DB2_NAME}'.")
        if _ask_yes_no(f"    Do you want to add them into '{DB2_NAME}.{COL2_NAME}'?"):
            _fetch_and_import(col1, col2, new_in_db1_ids, f"{DB1_NAME}.{COL1_NAME}", f"{DB2_NAME}.{COL2_NAME}")
        else:
            print(f"[i] Skipped adding domains to '{DB2_NAME}'.")
    else:
        print(f"\n[i] No new domains found in '{DB1_NAME}' that aren't already in '{DB2_NAME}'.")


if __name__ == "__main__":
    compare_databases()

