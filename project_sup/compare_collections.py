"""
project_sup/compare_collections.py — MongoDB Collection Comparison & Anomaly Exporter
========================================================================================

Compares the primary MongoDB collections:
  1. `domain_Listed` (source_domains) — Source queue of input domains.
  2. `checked_domains` — Processing results written by runner/classifiers.
  3. Screenshot archive on disk.

Filtering Rule (Audit Mode - Default):
  - Valid gambling sites WITH a screenshot are LEAVED OUT (omitted).
  - Included in the CSV:
      * Status is OTHER than gambling AND a screenshot is present (Anomaly).
      * Status IS gambling AND screenshot is NOT present (Missing Screenshot).
      * Domains in `domain_Listed` but not yet in `checked_domains` (Pending / Unchecked).
      * Domains in `checked_domains` not present in `domain_Listed`.

Pass `--all` to export all records without filtering.

Usage:
    python -m project_sup.compare_collections
    python -m project_sup.compare_collections --all
    python -m project_sup.compare_collections --output "output/collection_comparison.csv"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

import pandas as pd
from tqdm import tqdm
from db.mongo_client import get_db, source_domains, checked_domains
from export_domains.screenshot import all_filename_candidates

IST = timezone(timedelta(hours=5, minutes=30))


def clean_path_input(raw: str) -> str:
    """Clean path typed or pasted from terminal."""
    cleaned = raw.strip()
    if cleaned.startswith("& "):
        cleaned = cleaned[2:].strip()
    return cleaned.strip('"').strip("'")


def compare_and_export(output_path: str | None = None, limit: int = 0, export_all: bool = False) -> dict:
    get_db()

    # 1. Index screenshot files on disk
    ss_base = Path(os.getenv("SCREENSHOT_DIR", PROJECT_ROOT / "output" / "screenshots"))
    search_dirs = [ss_base, ss_base / "New folder"]
    disk_files: set[str] = set()
    for sdir in search_dirs:
        if sdir.is_dir():
            for fname in os.listdir(sdir):
                if fname.lower().endswith(".jpg"):
                    disk_files.add(fname)
    print(f"\n[screenshots] Indexed {len(disk_files):,} .jpg file(s) across screenshot directories.")

    # 2. Fetch source queue
    print("[collections] Fetching domains from `domain_Listed` (source_domains)...")
    cur_source = source_domains().find({}, {"_id": 1, "domain": 1, "processed": 1, "active": 1})
    if limit:
        cur_source = cur_source.limit(limit)
    source_docs = {
        (doc.get("domain") or str(doc["_id"])).strip().lower(): doc
        for doc in cur_source
        if (doc.get("domain") or str(doc.get("_id")))
    }
    print(f"[collections] Indexed {len(source_docs):,} domain(s) from `domain_Listed`.")

    # 3. Fetch checked results
    print("[collections] Fetching domains from `checked_domains`...")
    cur_checked = checked_domains().find({}, {
        "_id": 1, "domain": 1, "status": 1, "reason": 1, "url": 1,
        "screenshot_taken": 1, "decided_by": 1, "added_date": 1, "last_checked_at": 1
    })
    if limit:
        cur_checked = cur_checked.limit(limit)
    checked_docs = {
        (doc.get("domain") or str(doc["_id"])).strip().lower(): doc
        for doc in cur_checked
        if (doc.get("domain") or str(doc.get("_id")))
    }
    print(f"[collections] Indexed {len(checked_docs):,} domain(s) from `checked_domains`.")

    source_set = set(source_docs.keys())
    checked_set = set(checked_docs.keys())

    all_unique_domains = sorted(source_set | checked_set)
    in_both = source_set & checked_set
    only_in_source = source_set - checked_set
    only_in_checked = checked_set - source_set

    print(f"\n" + "=" * 65)
    print("           COLLECTION COMPARISON MATRIX")
    print("=" * 65)
    print(f"  Total Unique Domains across both collections : {len(all_unique_domains):,}")
    print(f"  Domains in `domain_Listed` (Source)          : {len(source_set):,}")
    print(f"  Domains in `checked_domains` (Results)       : {len(checked_set):,}")
    print(f"  Matching (Present in BOTH collections)       : {len(in_both):,}")
    print(f"  Unique to `domain_Listed` (Pending / Unchecked): {len(only_in_source):,}")
    print(f"  Unique to `checked_domains` (Direct results) : {len(only_in_checked):,}")
    print("=" * 65 + "\n")

    records = []
    omitted_clean_gambling = 0
    non_gambling_with_ss = 0
    gambling_missing_ss = 0

    for domain in tqdm(all_unique_domains, desc="Auditing Domains", unit="dom"):
        s_doc = source_docs.get(domain, {})
        c_doc = checked_docs.get(domain, {})

        in_source = domain in source_set
        in_checked = domain in checked_set

        status = c_doc.get("status", "unchecked") if in_checked else "unchecked"
        url = c_doc.get("url") or f"https://{domain}"
        ss_flag = bool(c_doc.get("screenshot_taken"))

        # Check if screenshot exists on disk
        cands = all_filename_candidates(url, domain)
        file_on_disk = next((c for c in cands if c in disk_files), None)
        has_screenshot = ss_flag or (file_on_disk is not None)

        # Classify presence / anomaly category
        if in_source and not in_checked:
            issue_category = "Pending / Unchecked (Only in domain_Listed)"
        elif in_checked and not in_source:
            issue_category = "Only in checked_domains"
        elif status == "gambling" and has_screenshot:
            issue_category = "Clean Gambling (Screenshot Verified)"
            omitted_clean_gambling += 1
            if not export_all:
                continue  # User rule: If gambling site and screenshot is there, LEAVE IT
        elif status != "gambling" and has_screenshot:
            issue_category = "Non-gambling with Screenshot"
            non_gambling_with_ss += 1
        elif status == "gambling" and not has_screenshot:
            issue_category = "Gambling Missing Screenshot"
            gambling_missing_ss += 1
        else:
            issue_category = f"Non-gambling ({status})"
            if not export_all:
                continue  # Normal clean non-gambling without screenshot -> omit from anomaly report

        records.append({
            "Domain": domain,
            "Issue_Category": issue_category,
            "Checked_Status": status,
            "Screenshot_Taken_DB": ss_flag,
            "Screenshot_File_On_Disk": file_on_disk or "",
            "In_Domain_Listed": in_source,
            "In_Checked_Domains": in_checked,
            "Decided_By": c_doc.get("decided_by", "N/A"),
            "Checked_Reason": c_doc.get("reason", "N/A"),
            "Source_Active": s_doc.get("active", "N/A"),
            "Source_Processed": s_doc.get("processed", "N/A"),
            "Last_Checked_At": c_doc.get("last_checked_at") or c_doc.get("added_date") or "",
        })

    # Print Audit Summary
    print(f"\n" + "=" * 65)
    print("              SCREENSHOT AUDIT SUMMARY")
    print("=" * 65)
    print(f"  Clean Gambling with Screenshot (Omitted from CSV) : {omitted_clean_gambling:,}")
    print(f"  Non-gambling with Screenshot (Added to CSV)       : {non_gambling_with_ss:,}")
    print(f"  Gambling Missing Screenshot (Added to CSV)        : {gambling_missing_ss:,}")
    print(f"  Pending / Unchecked in Source (Added to CSV)      : {len(only_in_source):,}")
    print(f"  Total Anomaly Records Exported to CSV             : {len(records):,}")
    print("=" * 65 + "\n")

    # Export to CSV
    now_str = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    if not output_path:
        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        mode_prefix = "all_collections" if export_all else "collection_anomalies"
        csv_path = out_dir / f"{mode_prefix}_{now_str}.csv"
    else:
        out_p = Path(clean_path_input(output_path))
        out_p.parent.mkdir(parents=True, exist_ok=True)
        csv_path = out_p.with_suffix(".csv")

    df = pd.DataFrame(records)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[collections] Exported report ({len(records):,} records) to:\n  -> {csv_path.resolve()}\n")

    return {
        "total_unique": len(all_unique_domains),
        "omitted_clean_gambling": omitted_clean_gambling,
        "non_gambling_with_ss": non_gambling_with_ss,
        "gambling_missing_ss": gambling_missing_ss,
        "records_exported": len(records),
        "csv_path": str(csv_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Check and match `domain_Listed` and `checked_domains` collections and audit screenshot anomalies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", "-o", default=None, help="Output CSV path")
    parser.add_argument("--limit", type=int, default=0, help="Limit records fetched from each collection (0 = all)")
    parser.add_argument("--all", action="store_true", help="Export all records including clean gambling sites")
    args = parser.parse_args()

    compare_and_export(output_path=args.output, limit=args.limit, export_all=args.all)


if __name__ == "__main__":
    main()
