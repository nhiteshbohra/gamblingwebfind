"""
audit_db.py — Comprehensive MongoDB audit and cleanup tool.

Audits schema integrity and field bloat in both MongoDB collections (domain_Listed & checked_domains).
If issues are found, prompts the user to automatically clean up and repair the database.

Usage:
    python project_sup/helping_code/audit_db.py          # Audit and prompt for cleanup
    python project_sup/helping_code/audit_db.py --fix    # Audit and automatically fix
"""
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# Load .env settings
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

MONGO_URI          = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME            = os.getenv("MONGO_DB_NAME", "gamblingsites")
COL_SOURCE_NAME    = os.getenv("MONGO_COLLECTION", "domain_Listed")
COL_CHECKED_NAME   = os.getenv("CHECKED_COLLECTION", "checked_domains")


def _ask_yes_no(prompt: str) -> bool:
    """Prompt user for yes/no. Returns True for yes, False for no."""
    while True:
        answer = input(f"{prompt} [y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("    Please enter 'y' or 'n'.")


def section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def run_db_health_check(auto_fix: bool = False):
    print(f"[+] Connecting to MongoDB at: {MONGO_URI}")
    print(f"[+] Target Database: '{DB_NAME}'")

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col_checked = db[COL_CHECKED_NAME]
    col_source = db[COL_SOURCE_NAME]

    issues = {}
    total_issues = 0

    # ── 1. checked_domains Overview ──────────────────────────────────────────
    section(f"COLLECTION: {COL_CHECKED_NAME} — OVERVIEW")
    total_checked  = col_checked.count_documents({})
    gambling_count = col_checked.count_documents({"status": "gambling"})
    regular_count  = col_checked.count_documents({"status": "regular"})
    blocked_count  = col_checked.count_documents({"status": "blocked"})
    dead_count     = col_checked.count_documents({"status": "dead"})

    print(f"  Total docs:   {total_checked:,}")
    print(f"  * gambling:   {gambling_count:,}")
    print(f"  * regular:    {regular_count:,}")
    print(f"  * blocked:    {blocked_count:,}")
    print(f"  * dead:       {dead_count:,}")

    # ── 2. Bloat Fields ───────────────────────────────────────────────────────
    section("AUDIT: FIELD BLOAT (checked_at, last_updated_at)")

    checked_at_count = col_checked.count_documents({"checked_at": {"$exists": True}})
    last_updated_count = col_checked.count_documents({"last_updated_at": {"$exists": True}})
    issues["checked_at"] = checked_at_count
    issues["last_updated_at"] = last_updated_count

    print(f"  {'[!]' if checked_at_count else '[OK]'} Docs with 'checked_at':      {checked_at_count:,}")
    print(f"  {'[!]' if last_updated_count else '[OK]'} Docs with 'last_updated_at': {last_updated_count:,}")

    # ── 3. Screenshot Fields on Non-Gambling Docs ──────────────────────────────
    section("AUDIT: SCREENSHOT FIELDS ON NON-GAMBLING DOCS")

    non_gambling_ss_count = col_checked.count_documents({
        "status": {"$ne": "gambling"},
        "$or": [
            {"screenshot_taken": {"$exists": True}},
            {"screenshot_failed_reason": {"$exists": True}},
        ]
    })
    issues["non_gambling_ss"] = non_gambling_ss_count
    print(f"  {'[!]' if non_gambling_ss_count else '[OK]'} Non-gambling docs with screenshot fields: {non_gambling_ss_count:,}")

    # ── 4. Export Integrity ───────────────────────────────────────────────────
    section("AUDIT: EXPORT INTEGRITY")

    exported_uncaptured_count = col_checked.count_documents({
        "status": "gambling",
        "exported": True,
        "screenshot_taken": False,
    })
    exported_non_gambling_count = col_checked.count_documents({
        "status": {"$ne": "gambling"},
        "exported": True,
    })
    issues["exported_uncaptured"] = exported_uncaptured_count
    issues["exported_non_gambling"] = exported_non_gambling_count

    print(f"  {'[!]' if exported_uncaptured_count else '[OK]'} Exported=True on uncaptured gambling docs: {exported_uncaptured_count:,}")
    print(f"  {'[!]' if exported_non_gambling_count else '[OK]'} Exported=True on non-gambling docs:         {exported_non_gambling_count:,}")

    # ── 5. domain_Listed Integrity ───────────────────────────────────────────
    section(f"COLLECTION: {COL_SOURCE_NAME} — FIELD INTEGRITY")

    total_source = col_source.count_documents({})
    no_processed = col_source.count_documents({"processed": {"$exists": False}})
    no_active    = col_source.count_documents({"active": {"$exists": False}})
    no_domain    = col_source.count_documents({"domain": {"$exists": False}})

    issues["no_processed"] = no_processed
    issues["no_active"]    = no_active
    issues["no_domain"]    = no_domain

    print(f"  Total docs: {total_source:,}")
    print(f"  {'[!]' if no_processed else '[OK]'} Docs missing 'processed' field: {no_processed:,}")
    print(f"  {'[!]' if no_active else '[OK]'} Docs missing 'active' field:    {no_active:,}")
    print(f"  {'[!]' if no_domain else '[OK]'} Docs missing 'domain' field:    {no_domain:,}")

    total_issues = sum(issues.values())

    # ── 6. Audit Summary & Cleanup Prompt ────────────────────────────────────
    section("SUMMARY")
    if total_issues == 0:
        print("  [OK] Database is 100% clean! No schema violations or bloat fields found.")
        print("=" * 60 + "\n")
        return

    print(f"  [!!] Total issues/violations found: {total_issues:,}")
    print("=" * 60)

    should_fix = auto_fix
    if not should_fix:
        should_fix = _ask_yes_no("\n[?] Do you want to fix and clean up all database issues now?")

    if not should_fix:
        print("[i] Cleanup cancelled. No changes were made to the database.\n")
        return

    # ── 7. Perform Cleanup ────────────────────────────────────────────────────
    section("PERFORMING DATABASE CLEANUP & REPAIR")
    total_fixed = 0

    if issues["checked_at"]:
        res = col_checked.update_many({"checked_at": {"$exists": True}}, {"$unset": {"checked_at": ""}})
        print(f"  [FIX] Removed 'checked_at' from {res.modified_count:,} docs")
        total_fixed += res.modified_count

    if issues["last_updated_at"]:
        res = col_checked.update_many({"last_updated_at": {"$exists": True}}, {"$unset": {"last_updated_at": ""}})
        print(f"  [FIX] Removed 'last_updated_at' from {res.modified_count:,} docs")
        total_fixed += res.modified_count

    if issues["non_gambling_ss"]:
        res = col_checked.update_many(
            {
                "status": {"$ne": "gambling"},
                "$or": [
                    {"screenshot_taken": {"$exists": True}},
                    {"screenshot_failed_reason": {"$exists": True}},
                ]
            },
            {"$unset": {"screenshot_taken": "", "screenshot_failed_reason": ""}}
        )
        print(f"  [FIX] Removed screenshot fields from {res.modified_count:,} non-gambling docs")
        total_fixed += res.modified_count

    if issues["exported_uncaptured"]:
        res = col_checked.update_many(
            {"status": "gambling", "exported": True, "screenshot_taken": False},
            {"$unset": {"exported": "", "exported_at": ""}}
        )
        print(f"  [FIX] Removed 'exported' from {res.modified_count:,} uncaptured gambling docs")
        total_fixed += res.modified_count

    if issues["exported_non_gambling"]:
        res = col_checked.update_many(
            {"status": {"$ne": "gambling"}, "exported": {"$exists": True}},
            {"$unset": {"exported": "", "exported_at": ""}}
        )
        print(f"  [FIX] Removed 'exported' from {res.modified_count:,} non-gambling docs")
        total_fixed += res.modified_count

    if issues["no_processed"]:
        res = col_source.update_many({"processed": {"$exists": False}}, {"$set": {"processed": False}})
        print(f"  [FIX] Set processed=False on {res.modified_count:,} docs")
        total_fixed += res.modified_count

    if issues["no_active"]:
        res = col_source.update_many({"active": {"$exists": False}}, {"$set": {"active": True}})
        print(f"  [FIX] Set active=True on {res.modified_count:,} docs")
        total_fixed += res.modified_count

    if issues["no_domain"]:
        res = col_source.update_many({"domain": {"$exists": False}}, [{"$set": {"domain": "$_id"}}])
        print(f"  [FIX] Set domain=_id on {res.modified_count:,} docs")
        total_fixed += res.modified_count

    print("\n" + "=" * 60)
    print(f"  [OK] CLEANUP COMPLETE! Fixed {total_fixed:,} total document issues.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit and clean up MongoDB database schema.")
    parser.add_argument("--fix", action="store_true", help="Automatically clean up issues without asking")
    args = parser.parse_args()

    run_db_health_check(auto_fix=args.fix)
