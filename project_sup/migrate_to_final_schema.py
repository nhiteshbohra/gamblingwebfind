"""
project_sup/migrate_to_final_schema.py
──────────────────────────────────────
Migrates all documents in checked_domains and domain_Listed
to the strict, final project schema:

1. checked_domains:
   - Always: _id, domain, url, status, reason, added_date, source
   - Only on status == 'gambling': screenshot_taken, screenshot_date, exported, exported_at
   - Unsets: screenshot_failed_reason, ai_evaluated, and screenshot fields on non-gambling docs.

2. domain_Listed:
   - Always: _id, domain, active, processed, added_date, source
   - Unsets: block_reason, exported, reason, screenshot_taken, status.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, get_db


def migrate():
    get_db()
    print("[+] Starting migration to strict final schema...\n")

    # ── 1. Clean checked_domains ──────────────────────────────────────────
    print("[1/4] Unsetting extra fields (screenshot_failed_reason, ai_evaluated) from checked_domains...")
    res1 = checked_domains().update_many(
        {"$or": [
            {"screenshot_failed_reason": {"$exists": True}},
            {"ai_evaluated": {"$exists": True}},
        ]},
        {"$unset": {
            "screenshot_failed_reason": "",
            "ai_evaluated": "",
        }}
    )
    print(f"  -> Cleaned {res1.modified_count:,} documents in checked_domains.")

    print("\n[2/4] Removing screenshot/export fields from non-gambling documents in checked_domains...")
    res2 = checked_domains().update_many(
        {
            "status": {"$ne": "gambling"},
            "$or": [
                {"screenshot_taken": {"$exists": True}},
                {"screenshot_date": {"$exists": True}},
                {"exported": {"$exists": True}},
                {"exported_at": {"$exists": True}},
            ]
        },
        {"$unset": {
            "screenshot_taken": "",
            "screenshot_date": "",
            "exported": "",
            "exported_at": "",
        }}
    )
    print(f"  -> Cleaned {res2.modified_count:,} non-gambling documents in checked_domains.")

    # ── 2. Clean domain_Listed ────────────────────────────────────────────
    print("\n[3/4] Unsetting extra fields (block_reason, exported, reason, screenshot_taken, status) from domain_Listed...")
    res3 = source_domains().update_many(
        {"$or": [
            {"block_reason": {"$exists": True}},
            {"exported": {"$exists": True}},
            {"reason": {"$exists": True}},
            {"screenshot_taken": {"$exists": True}},
            {"status": {"$exists": True}},
        ]},
        {"$unset": {
            "block_reason": "",
            "exported": "",
            "reason": "",
            "screenshot_taken": "",
            "status": "",
        }}
    )
    print(f"  -> Cleaned {res3.modified_count:,} documents in domain_Listed.")

    # ── 3. Ensure source is present ───────────────────────────────────────
    print("\n[4/4] Ensuring default source on documents missing source...")
    res4_cd = checked_domains().update_many(
        {"$or": [{"source": None}, {"source": {"$exists": False}}]},
        {"$set": {"source": "searxng_search"}}
    )
    res4_sd = source_domains().update_many(
        {"$or": [{"source": None}, {"source": {"$exists": False}}]},
        {"$set": {"source": "searxng_search"}}
    )
    print(f"  -> Stamped default source on {res4_cd.modified_count:,} checked_domains and {res4_sd.modified_count:,} domain_Listed.")

    print("\n" + "=" * 60)
    print("           SCHEMA MIGRATION COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    migrate()
