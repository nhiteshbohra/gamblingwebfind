import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME     = os.getenv("MONGO_DB_NAME", "gamblingsites")
COL_SOURCE  = os.getenv("MONGO_COLLECTION", "domain_Listed")
COL_CHECKED = os.getenv("CHECKED_COLLECTION", "checked_domains")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

col_source  = db[COL_SOURCE]
col_checked = db[COL_CHECKED]


print("=" * 60)
print("       DATABASE SYNC: domain_Listed <-> checked_domains")
print("=" * 60)

# 1. Fetch initial counts
total_source = col_source.count_documents({})
total_checked = col_checked.count_documents({})
unprocessed_before = col_source.count_documents({"processed": {"$ne": True}})

print(f"[+] Total domains in 'domain_Listed':  {total_source:,}")
print(f"[+] Total domains in 'checked_domains': {total_checked:,}")
print(f"[+] Unprocessed in 'domain_Listed' BEFORE sync: {unprocessed_before:,}")

# 2. Fetch checked domain IDs
print("\n[+] Fetching checked domain IDs...")
checked_ids = set(doc["_id"] for doc in col_checked.find({}, {"_id": 1}))

# 3. Find common domains where processed != True
print("[+] Finding common domains where 'processed' is False or missing...")
unprocessed_common = list(
    col_source.find(
        {"_id": {"$in": list(checked_ids)}, "processed": {"$ne": True}},
        {"_id": 1}
    )
)
common_ids = [doc["_id"] for doc in unprocessed_common]
print(f"    -> Found {len(common_ids):,} common domain(s) needing update.")

# 4. Update domains if any found
total_updated = 0
if common_ids:
    print(f"\n[+] Updating {len(common_ids):,} domains to processed=True...")
    chunk_size = 5000
    for i in range(0, len(common_ids), chunk_size):
        chunk = common_ids[i : i + chunk_size]
        res = col_source.update_many(
            {"_id": {"$in": chunk}},
            {"$set": {"processed": True}}
        )
        total_updated += res.modified_count
    print(f"[OK] Successfully updated {total_updated:,} domains in 'domain_Listed'.")
else:
    print("[i] No common domains needed updating (already in sync).")

# 5. Fetch remaining unprocessed count after sync
unprocessed_after = col_source.count_documents({"processed": {"$ne": True}})

print("\n" + "=" * 60)
print("                      SUMMARY OF CHANGES")
print("=" * 60)
print(f"  - Unprocessed BEFORE sync:  {unprocessed_before:,}")
print(f"  - Total Domains CHANGED:    {total_updated:,}")
print(f"  - Unprocessed AFTER sync:   {unprocessed_after:,}")
print("=" * 60)


