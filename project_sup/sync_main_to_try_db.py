"""
project_sup/sync_main_to_try_db.py — Synchronize Main Database to Try Database
================================================================================

Copies/updates collections from the primary MongoDB database (`gamblingsites`)
into the trial/staging database (`gamblingsitetry`).

Features:
  - High-performance chunked bulk writes (batches of 1,000).
  - Syncs `checked_domains` (default) or all primary collections (`--all`).
  - Optional `--mirror` flag to remove stale records from the target database
    so it becomes an exact 1:1 replica of the main database.

Usage:
  python -m project_sup.sync_main_to_try_db
  python -m project_sup.sync_main_to_try_db --mirror
  python -m project_sup.sync_main_to_try_db --collections checked_domains domain_Listed
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from pymongo import MongoClient, ReplaceOne
from tqdm import tqdm

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_SOURCE_DB = os.getenv("MONGO_DB_NAME", "gamblingsites")
DEFAULT_TARGET_DB = "gamblingsitetry"
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
BATCH_SIZE = 1000


def get_collection_stats(col) -> dict:
    total = col.count_documents({})
    gambling_cnt = col.count_documents({"status": "gambling"})
    ss_cnt = col.count_documents({"status": "gambling", "screenshot_taken": True})
    return {
        "total": total,
        "gambling": gambling_cnt,
        "screenshot_taken": ss_cnt,
    }


def sync_collection(
    client: MongoClient,
    src_db_name: str,
    tgt_db_name: str,
    col_name: str,
    mirror: bool = False,
):
    src_col = client[src_db_name][col_name]
    tgt_col = client[tgt_db_name][col_name]

    src_total = src_col.count_documents({})
    if src_total == 0:
        print(f"[-] Source collection '{src_db_name}.{col_name}' is empty. Skipping.")
        return

    print(f"\n" + "=" * 65)
    print(f"  Syncing: '{src_db_name}.{col_name}' -> '{tgt_db_name}.{col_name}'")
    print(f"  Source Documents: {src_total:,}")
    if mirror:
        print("  Mode: 1:1 MIRROR (Stale docs in target will be removed)")
    else:
        print("  Mode: UPSERT (Insert new docs & update existing)")
    print("=" * 65)

    # 1. If mirror mode, remove documents in target that are not in source
    if mirror:
        print("  [mirror] Indexing source IDs for pruning check...")
        src_ids = set(src_col.distinct("_id"))
        tgt_ids = set(tgt_col.distinct("_id"))
        stale_ids = list(tgt_ids - src_ids)
        if stale_ids:
            print(f"  [mirror] Removing {len(stale_ids):,} stale documents from target...")
            tgt_col.delete_many({"_id": {"$in": stale_ids}})
            print(f"  [mirror] Stale documents deleted successfully.")
        else:
            print("  [mirror] No stale documents found in target.")

    # 2. Bulk upsert/replace all source documents into target in batches
    cursor = src_col.find({})
    operations = []
    total_synced = 0
    t0 = time.time()

    with tqdm(total=src_total, desc=f"Syncing {col_name}", unit="doc") as pbar:
        for doc in cursor:
            operations.append(
                ReplaceOne({"_id": doc["_id"]}, doc, upsert=True)
            )

            if len(operations) >= BATCH_SIZE:
                tgt_col.bulk_write(operations, ordered=False)
                total_synced += len(operations)
                pbar.update(len(operations))
                operations = []

        if operations:
            tgt_col.bulk_write(operations, ordered=False)
            total_synced += len(operations)
            pbar.update(len(operations))

    elapsed = time.time() - t0
    print(f"[+] Successfully synced {total_synced:,} documents in {elapsed:.2f}s.")

    # 3. Print verified statistics comparison
    if col_name == "checked_domains":
        src_stats = get_collection_stats(src_col)
        tgt_stats = get_collection_stats(tgt_col)
        print("\n  Verified Stats:")
        print(f"    - Total Docs         : Source={src_stats['total']:,} | Target={tgt_stats['total']:,}")
        print(f"    - Gambling Docs      : Source={src_stats['gambling']:,} | Target={tgt_stats['gambling']:,}")
        print(f"    - Screenshot Flag    : Source={src_stats['screenshot_taken']:,} | Target={tgt_stats['screenshot_taken']:,}")


def run_sync(
    source_db: str = DEFAULT_SOURCE_DB,
    target_db: str = DEFAULT_TARGET_DB,
    collections: list[str] | None = None,
    mirror: bool = False,
    mongo_uri: str = DEFAULT_MONGO_URI,
):
    if source_db == target_db:
        print(f"[!] Error: Source database and target database cannot be the same ('{source_db}').")
        return

    if not collections:
        collections = ["checked_domains"]

    client = MongoClient(mongo_uri)
    print(f"Connected to MongoDB: {mongo_uri}")
    print(f"Source DB : '{source_db}'")
    print(f"Target DB : '{target_db}'")
    print(f"Collections: {', '.join(collections)}")

    for col in collections:
        sync_collection(client, source_db, target_db, col, mirror=mirror)

    print("\n[+] Database synchronization complete!\n")


def main():
    parser = argparse.ArgumentParser(
        description="Sync MongoDB collections from main database to try database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        "-s",
        default=DEFAULT_SOURCE_DB,
        help=f"Source database name (default: {DEFAULT_SOURCE_DB})",
    )
    parser.add_argument(
        "--target",
        "-t",
        default=DEFAULT_TARGET_DB,
        help=f"Target database name (default: {DEFAULT_TARGET_DB})",
    )
    parser.add_argument(
        "--collections",
        "-c",
        nargs="+",
        default=["checked_domains"],
        help="Collections to sync (default: checked_domains)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync both 'checked_domains' and 'domain_Listed'",
    )
    parser.add_argument(
        "--mirror",
        "-m",
        action="store_true",
        help="Make target an exact 1:1 replica of source (removes stale docs from target)",
    )
    args = parser.parse_args()

    cols = ["checked_domains", "domain_Listed"] if args.all else args.collections
    run_sync(
        source_db=args.source,
        target_db=args.target,
        collections=cols,
        mirror=args.mirror,
    )


if __name__ == "__main__":
    main()
