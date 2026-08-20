"""
project_sup/filter_exported_from_txt.py
───────────────────────────────────────
Compares domains from a text/list file against MongoDB and creates a new file.

Filtering Rules:
  • If domain is in MongoDB AND exported == True  -> REMOVED (already exported)
  • If domain is in MongoDB AND exported != True  -> KEPT    (present but not exported)
  • If domain is NOT in MongoDB                   -> KEPT    (not yet in DB)

Usage:
    python project_sup/filter_exported_from_txt.py
    python project_sup/filter_exported_from_txt.py --file path/to/domains.txt --output path/to/output.txt
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, get_db, extract_domains_from_file


def get_exported_domains_set() -> set:
    """Fetch all domain IDs from MongoDB where exported is True."""
    get_db()
    print("[+] Loading exported domains from MongoDB (checked_domains)...")
    exported_docs = checked_domains().find(
        {"exported": True},
        {"_id": 1, "domain": 1}
    )
    exported_set = set()
    for doc in exported_docs:
        d = doc.get("domain") or doc.get("_id")
        if d:
            exported_set.add(str(d).strip().lower().removeprefix("www.").rstrip("/"))
    print(f"[+] Found {len(exported_set):,} domains in MongoDB marked as exported: True")
    return exported_set


def filter_domains(input_file: str, output_file: str = None) -> tuple[int, int, int, str]:
    """
    Reads domains from input_file, filters out those where exported==True,
    and writes the remaining domains to output_file.
    
    Returns: (total_read, count_removed, count_kept, output_path)
    """
    input_path = Path(input_file)
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # Default output path if not specified
    if not output_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(PROJECT_ROOT / "output" / f"unexported_{input_path.stem}_{timestamp}.txt")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Get set of exported domains from MongoDB
    exported_set = get_exported_domains_set()

    # 2. Extract and deduplicate domains from the input file
    print(f"\n[+] Reading domains from: {input_path}")
    raw_domains = extract_domains_from_file(str(input_path))
    total_domains = len(raw_domains)
    print(f"[+] Total unique domains found in file: {total_domains:,}")

    if total_domains == 0:
        print("[!] No domains found to process.")
        return 0, 0, 0, str(output_path)

    # 3. Filter domains
    kept_domains = []
    removed_count = 0

    print("[+] Comparing against MongoDB exported status...")
    for domain in tqdm(raw_domains, desc="Filtering Domains", unit="domain"):
        clean_d = str(domain).strip().lower().removeprefix("www.").rstrip("/")
        if clean_d in exported_set:
            removed_count += 1
        else:
            kept_domains.append(clean_d)

    kept_count = len(kept_domains)

    # 4. Write kept domains to new output file
    print(f"\n[+] Writing {kept_count:,} remaining domains to: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        for domain in kept_domains:
            f.write(f"{domain}\n")

    # 5. Print Detailed Summary
    print("\n" + "=" * 60)
    print("           DOMAIN FILTERING SUMMARY")
    print("=" * 60)
    print(f"  Input File                     : {input_path.name}")
    print(f"  Total Unique Domains in File   : {total_domains:,}")
    print(f"  Removed (Exported == True)     : {removed_count:,}")
    print(f"  Kept (Unexported / New)        : {kept_count:,}")
    print(f"  New Output File Created        : {output_path}")
    print("=" * 60 + "\n")

    return total_domains, removed_count, kept_count, str(output_path)


def main():
    parser = argparse.ArgumentParser(description="Filter out already exported domains from a text file")
    parser.add_argument("--file", "-f", help="Path to input text/CSV/Excel file")
    parser.add_argument("--output", "-o", help="Path to write the new filtered file")
    args = parser.parse_args()

    input_file = args.file
    if not input_file:
        default_candidate = PROJECT_ROOT / "gambling_merged_unique_domains.txt"
        default_hint = f" [default: {default_candidate.name}]" if default_candidate.exists() else ""
        print("\n--- Filter Out Already Exported Domains ---")
        user_input = input(f"Enter path to domains file{default_hint}: ").strip().strip('"').strip("'")
        if not user_input and default_candidate.exists():
            input_file = str(default_candidate)
        else:
            input_file = user_input

    if not input_file:
        print("[!] No input file specified. Exiting.")
        return

    filter_domains(input_file=input_file, output_file=args.output)


if __name__ == "__main__":
    main()
