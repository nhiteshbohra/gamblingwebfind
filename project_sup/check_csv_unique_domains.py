"""
project_sup/check_csv_unique_domains.py — Check CSV Domains against MongoDB & Export Unique Sheet
==================================================================================================

Reads a CSV (or Excel) file containing domains/URLs, checks each domain against MongoDB:
  Database  : MONGO_DB_NAME (default: gamblingsitetry)
  Collection: MONGO_COLLECTION (default: domain_Listed_git)

Generates an Excel workbook with:
  1. `All_Domains` (or original sheet) with an `in_mongo` status column.
  2. `unique` sheet containing ONLY the domains that were NOT found in the collection.
Also exports a clean `unique_domains.csv` for quick downstream usage.

Usage:
    python -m project_sup.check_csv_unique_domains
    python -m project_sup.check_csv_unique_domains --file "path/to/domains.csv"
    python -m project_sup.check_csv_unique_domains -f "path/to/domains.csv" -o "output/result.xlsx"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, List, Set, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

import pandas as pd
import tldextract
from tqdm import tqdm
from pymongo import MongoClient

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_DB_NAME = os.getenv("MONGO_DB_NAME", "gamblingsitetry")
DEFAULT_COLLECTION_NAME = os.getenv("MONGO_COLLECTION", "domain_Listed_git")
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")


def clean_path_input(raw: str) -> str:
    """Clean a file path typed or pasted from Windows Explorer / PowerShell."""
    cleaned = raw.strip()
    if cleaned.startswith("& "):
        cleaned = cleaned[2:].strip()
    return cleaned.strip('"').strip("'")


def clean_domain_string(raw: Any) -> str:
    """
    Clean and extract domain name from a raw string/URL.
    Handles:
      - https://www.example.com/path?q=1 -> example.com
      - www.example.com/login -> example.com
      - example.com:8080 -> example.com
      - sub.example.co.uk -> sub.example.co.uk or example.co.uk
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text.startswith("#"):
        return ""

    # Remove quotes, brackets, spaces
    text = text.strip("\"'<>[](){},; \t\r\n")

    # If full URL, parse via urllib
    if "://" in text:
        try:
            parsed = urllib.parse.urlparse(text)
            text = parsed.netloc or parsed.path
        except Exception:
            pass

    # Strip scheme if without slashes (e.g. http:www.domain.com)
    text = re.sub(r'^(https?|ftp)://?', '', text, flags=re.IGNORECASE)

    # Strip port if any
    if ":" in text:
        text = text.split(":")[0]

    # Strip path or query or fragment
    for sep in ("/", "?", "#"):
        if sep in text:
            text = text.split(sep)[0]

    # Lowercase & strip www.
    text = text.lower().strip()
    if text.startswith("www."):
        text = text[4:]

    # Strip any trailing dots or spaces
    text = text.strip(". ")

    # Basic domain validity check (must have at least one dot and valid chars)
    if "." not in text or " " in text:
        return ""

    # Reject common invalid strings
    if text.endswith(".") or text.startswith("."):
        return ""

    return text


def detect_domain_column(df: pd.DataFrame) -> str:
    """
    Automatically identify the column containing domains/URLs.
    First checks known column name keywords, then analyzes values.
    """
    col_names_lower = [str(c).lower().strip() for c in df.columns]

    priority_names = [
        "domain", "domains", "domain_name", "url", "urls", "website",
        "websites", "site", "sites", "host", "hostname", "link", "links", "web"
    ]

    for target in priority_names:
        for idx, col_name in enumerate(col_names_lower):
            if col_name == target or target in col_name:
                return str(df.columns[idx])

    # Sample rows to find the column with the most domain-like values
    best_col = None
    best_score = -1

    for col in df.columns:
        score = 0
        sample = df[col].dropna().astype(str).head(30)
        for val in sample:
            cleaned = clean_domain_string(val)
            if cleaned and "." in cleaned:
                score += 1
        if score > best_score:
            best_score = score
            best_col = str(col)

    return best_col if best_col is not None else str(df.columns[0])


def load_input_file(file_path: Path) -> Tuple[pd.DataFrame, str]:
    """
    Load CSV or Excel file into a DataFrame, trying multiple encodings for CSV.
    Returns (DataFrame, detected_extension).
    """
    suffix = file_path.suffix.lower()

    if suffix in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(file_path)
        return df, suffix

    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin1"]
    last_err = None

    for enc in encodings:
        try:
            # Try comma separator first, then let pandas auto-detect if single column
            df = pd.read_csv(file_path, encoding=enc)
            # If pandas read the entire line into 1 column with semicolons or tabs
            if len(df.columns) == 1 and ";" in str(df.columns[0]):
                df = pd.read_csv(file_path, sep=";", encoding=enc)
            elif len(df.columns) == 1 and "\t" in str(df.columns[0]):
                df = pd.read_csv(file_path, sep="\t", encoding=enc)
            return df, ".csv"
        except Exception as e:
            last_err = e
            continue

    raise ValueError(f"Could not read {file_path} with any supported encoding: {last_err}")


def get_mongo_collection(db_name: str, col_name: str, uri: str):
    """Connect to MongoDB and return the specified collection."""
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=30000,
        maxPoolSize=50,
        retryWrites=True,
    )
    # Quick ping test
    client.admin.command("ping")
    db = client[db_name]
    return client, db[col_name]


def query_domains_in_mongo(collection, domains_list: List[str], batch_size: int = 2000) -> Set[str]:
    """
    Batch query MongoDB to find which domains exist in the collection.
    Checks both `_id` and `domain` fields.
    Returns a set of domains found in MongoDB.
    """
    found: Set[str] = set()
    unique_to_query = list(dict.fromkeys(domains_list))

    print(f"[mongo] Checking {len(unique_to_query):,} unique domain(s) against '{collection.name}'...")

    for i in tqdm(range(0, len(unique_to_query), batch_size), desc="Checking MongoDB", unit="batch"):
        batch = unique_to_query[i : i + batch_size]

        # 1. Primary check: _id field (indexed and fast)
        cur_id = collection.find({"_id": {"$in": batch}}, {"_id": 1})
        batch_found = {str(doc["_id"]).lower() for doc in cur_id if doc.get("_id")}
        found.update(batch_found)

        # 2. Secondary check for domains not matched on _id: check `domain` field
        remaining = [d for d in batch if d not in batch_found]
        if remaining:
            cur_dom = collection.find({"domain": {"$in": remaining}}, {"domain": 1, "_id": 1})
            for doc in cur_dom:
                if doc.get("domain"):
                    found.add(str(doc["domain"]).lower())
                elif doc.get("_id"):
                    found.add(str(doc["_id"]).lower())

    return found


def process_csv_and_generate_workbook(
    file_path: str | Path,
    output_path: str | Path | None = None,
    domain_column: str | None = None,
    db_name: str = DEFAULT_DB_NAME,
    col_name: str = DEFAULT_COLLECTION_NAME,
    mongo_uri: str = DEFAULT_MONGO_URI,
) -> dict:
    """
    Main processing pipeline:
      1. Loads CSV or Excel file.
      2. Detects & extracts domain column.
      3. Queries MongoDB collection (`domain_Listed_git`).
      4. Creates/updates Excel workbook with `All_Domains` and `unique` sheets.
      5. Exports standalone `unique_domains.csv`.
    """
    in_file = Path(file_path).resolve()
    if not in_file.exists():
        raise FileNotFoundError(f"Input file not found: {in_file}")

    print("\n" + "=" * 70)
    print("        CHECK CSV UNIQUE DOMAINS AGAINST MONGODB")
    print("=" * 70)
    print(f"  Input File  : {in_file}")
    print(f"  Database    : {db_name}")
    print(f"  Collection  : {col_name}")
    print("=" * 70 + "\n")

    # 1. Load File
    df, ext = load_input_file(in_file)
    total_rows = len(df)
    print(f"[file] Loaded {total_rows:,} row(s) from {in_file.name}")

    if total_rows == 0:
        print("[file] Input file is empty. Nothing to process.")
        return {}

    # 2. Determine Domain Column
    col_to_use = domain_column or detect_domain_column(df)
    if col_to_use not in df.columns:
        raise ValueError(f"Column '{col_to_use}' not found in file. Available columns: {list(df.columns)}")
    print(f"[file] Using column: '{col_to_use}' for domain matching")

    # 3. Clean & Extract Domains
    cleaned_domains: List[str] = []
    for val in df[col_to_use]:
        cleaned_domains.append(clean_domain_string(val))

    df["_cleaned_domain"] = cleaned_domains
    valid_domains = [d for d in cleaned_domains if d]
    distinct_domains = set(valid_domains)
    print(f"[file] Extracted {len(valid_domains):,} valid domain(s) ({len(distinct_domains):,} distinct).")

    # 4. Query MongoDB
    client, collection = get_mongo_collection(db_name, col_name, mongo_uri)
    found_in_mongo = query_domains_in_mongo(collection, valid_domains)
    client.close()

    # 5. Mark Status
    # in_mongo: True if found in MongoDB, False otherwise (Unique)
    df["in_mongo"] = df["_cleaned_domain"].apply(lambda d: bool(d and d in found_in_mongo))
    df["status"] = df["in_mongo"].apply(lambda x: "FOUND_IN_MONGO" if x else "UNIQUE_NOT_FOUND")

    # 6. Extract Unique Rows & Domains
    # Unique = rows where cleaned domain is valid AND not found in MongoDB
    df_unique_rows = df[(df["_cleaned_domain"] != "") & (~df["in_mongo"])].copy()

    # Create a deduplicated unique domains dataframe
    unique_domains_list = sorted(list(distinct_domains - found_in_mongo))
    df_unique_sheet = df_unique_rows.drop(columns=["in_mongo", "status"], errors="ignore")
    # Move _cleaned_domain to first column for clarity
    cols = ["_cleaned_domain"] + [c for c in df_unique_sheet.columns if c != "_cleaned_domain"]
    df_unique_sheet = df_unique_sheet[cols].rename(columns={"_cleaned_domain": "unique_domain"})

    # Prepare All Domains sheet
    df_all_sheet = df.drop(columns=["_cleaned_domain"], errors="ignore")

    # 7. Determine Output Paths
    if output_path:
        out_xlsx = Path(output_path).resolve()
        if out_xlsx.suffix.lower() != ".xlsx":
            out_xlsx = out_xlsx.with_suffix(".xlsx")
    else:
        # Default: if input is .csv, output as <stem>.xlsx (or <stem>_checked.xlsx if stem.xlsx exists)
        if ext in (".xlsx", ".xlsm"):
            out_xlsx = in_file
        else:
            cand_xlsx = in_file.with_suffix(".xlsx")
            out_xlsx = cand_xlsx

    # Standalone CSV path for unique domains
    unique_csv_path = in_file.parent / f"{in_file.stem}_unique.csv"

    # 8. Write Excel Workbook with 'All_Domains' and 'unique' sheets
    print(f"\n[export] Writing workbook: {out_xlsx} ...")
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)

    if ext in (".xlsx", ".xlsm") and out_xlsx == in_file:
        import openpyxl
        wb = openpyxl.load_workbook(out_xlsx)
        # Remove old 'unique' sheet if it already exists
        if "unique" in wb.sheetnames:
            del wb["unique"]
        wb.save(out_xlsx)
        wb.close()

        with pd.ExcelWriter(out_xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df_unique_sheet.to_excel(writer, sheet_name="unique", index=False)
            if "All_Domains" not in writer.sheets:
                df_all_sheet.to_excel(writer, sheet_name="All_Domains", index=False)
    else:
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
            df_all_sheet.to_excel(writer, sheet_name="All_Domains", index=False)
            df_unique_sheet.to_excel(writer, sheet_name="unique", index=False)

    # 9. Also write standalone CSV of unique domains
    df_unique_sheet.to_csv(unique_csv_path, index=False, encoding="utf-8-sig")
    print(f"[export] Writing unique domains CSV: {unique_csv_path} ...")

    # 10. Summary
    count_found = len(found_in_mongo)
    count_unique = len(unique_domains_list)
    total_distinct = len(distinct_domains)
    pct_found = (count_found / total_distinct * 100) if total_distinct else 0.0
    pct_unique = (count_unique / total_distinct * 100) if total_distinct else 0.0

    print("\n" + "=" * 70)
    print("                    PROCESSING SUMMARY")
    print("=" * 70)
    print(f"  Input File              : {in_file}")
    print(f"  Total Input Rows        : {total_rows:,}")
    print(f"  Valid Domains Extracted : {len(valid_domains):,}")
    print(f"  Distinct Unique in File : {total_distinct:,}")
    print(f"  -------------------------------------------------------------")
    print(f"  Found in MongoDB        : {count_found:,} ({pct_found:.1f}%) -> in collection '{col_name}'")
    print(f"  Unique (NOT in MongoDB) : {count_unique:,} ({pct_unique:.1f}%) -> new unique domains")
    print(f"  -------------------------------------------------------------")
    print(f"  Excel Workbook Output   : {out_xlsx}")
    print(f"    - Sheet 'All_Domains' : Full records with 'in_mongo' & 'status'")
    print(f"    - Sheet 'unique'      : {count_unique:,} unique domains NOT in collection")
    print(f"  Unique Domains CSV      : {unique_csv_path}")
    print("=" * 70 + "\n")

    return {
        "total_rows": total_rows,
        "valid_domains": len(valid_domains),
        "distinct_domains": total_distinct,
        "found_in_mongo": count_found,
        "unique_not_found": count_unique,
        "workbook_path": str(out_xlsx),
        "unique_csv_path": str(unique_csv_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Check domains from CSV/Excel against MongoDB and create an Excel workbook with a 'unique' sheet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "file_pos",
        nargs="?",
        default=None,
        help="Path to CSV or Excel file (positional)",
    )
    parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Path to CSV or Excel file",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional path for output Excel workbook (.xlsx)",
    )
    parser.add_argument(
        "--column",
        "-c",
        default=None,
        help="Column name containing domains/URLs (auto-detected if omitted)",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_NAME,
        help=f"MongoDB database name [default: {DEFAULT_DB_NAME}]",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help=f"MongoDB collection name [default: {DEFAULT_COLLECTION_NAME}]",
    )
    parser.add_argument(
        "--uri",
        default=DEFAULT_MONGO_URI,
        help="MongoDB connection URI",
    )

    args = parser.parse_args()

    file_arg = args.file or args.file_pos
    if not file_arg:
        print("\n" + "-" * 60)
        print("  Check CSV Domains Against MongoDB Collection")
        print(f"  Target Collection: {args.db}.{args.collection}")
        print("-" * 60)
        prompt_text = "Enter CSV or Excel file path: "
        try:
            typed = clean_path_input(input(prompt_text))
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return
        file_arg = typed

    if not file_arg:
        print("Error: No file path provided.")
        return

    process_csv_and_generate_workbook(
        file_path=file_arg,
        output_path=args.output,
        domain_column=args.column,
        db_name=args.db,
        col_name=args.collection,
        mongo_uri=args.uri,
    )


if __name__ == "__main__":
    main()
