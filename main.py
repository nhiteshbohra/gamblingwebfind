"""
main.py — Single entry point for gamblingwebfind.

Config from root .env — no settings.yaml dependency.

Usage:
  python main.py
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db.mongo_client import get_db

PROJECT_ROOT = Path(__file__).resolve().parent
KEYWORDS_FILE = PROJECT_ROOT / "gambling_top_500_keywords.json"


def _load_500_keywords() -> list[str]:
    """Load all keywords from gambling_top_500_keywords.json."""
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        terms = json.load(f)
    return [str(k).strip().lower() for k in terms if str(k).strip()]


def run_searxng_search():
    print("\n--- keywordssearch ---")

    # Auto-load from gambling_top_500_keywords.json
    keywords = _load_500_keywords()
    print(f"[+] Loaded {len(keywords)} keywords from {KEYWORDS_FILE.name}")
    print(f"    First 10: {', '.join(keywords[:10])} ...")

    from keywordssearch.searxng_search import run_search
    summary = asyncio.run(run_search(keywords))
    print(
        f"\n[+] keywordssearch complete.\n"
        f"    Raw URLs found   : {summary['total_urls']}\n"
        f"    Unique domains   : {summary['unique_domains']}\n"
        f"    New in MongoDB   : {summary['new_inserted']}"
    )


def run_keywords_search():
    print("\n--- keywordsindomainfetch ---")
    keywords_raw = input(
        "Enter keyword(s) to search (single or comma-separated, e.g. bet, casino, slot, spin): "
    ).strip()

    if not keywords_raw:
        print("[!] No keywords provided. Returning to menu.")
        return

    kw_list = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    if not kw_list:
        print("[!] Invalid keywords provided. Returning to menu.")
        return

    print(f"\n[+] Starting keywordsindomainfetch for: {', '.join(kw_list)}")
    find_domains_script = PROJECT_ROOT / "keywordsindomainfetch" / "find_domains.py"

    cmd = [sys.executable, str(find_domains_script), "--keywords"] + kw_list
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] keywordsindomainfetch error: exit code {e.returncode}")
    except KeyboardInterrupt:
        print("\n[!] keywordsindomainfetch interrupted by user.")


def run_checking_url():
    print("\n--- checking_url ---")
    from checking_url.url_checker import process_domains

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    xlsx_path = f"output/checking_results_{run_ts}.xlsx"

    concurrency = int(os.getenv("CHECK_CONCURRENCY", 50))
    limit = int(os.getenv("CHECK_LIMIT", 0))

    summary = asyncio.run(process_domains(concurrency=concurrency, limit=limit, output_excel=xlsx_path))
    print(f"[+] checking_url complete. Summary: {summary}")


def run_capture_url():
    print("\n--- capture_url ---")
    from capture_url.screenshot import process_domains as capture_run
    from capture_url.excel_exporter import export_capture_workbook
    from capture_url.docx_report_generator import build_report_from_mongo

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    processed_ids = asyncio.run(capture_run(concurrency=conc, limit=0))

    xlsx_path = f"output/capture_results_{run_ts}.xlsx"
    docx_path = f"output/capture_report_{run_ts}.docx"

    export_capture_workbook(domain_ids=processed_ids, output_path=xlsx_path)
    build_report_from_mongo(domain_ids=processed_ids, output_path=docx_path, cleanup=True)
    print(f"[+] capture_url complete. Saved Excel: {xlsx_path}, Word: {docx_path}")


def interactive_menu():
    while True:
        print("\n" + "=" * 55)
        print("           GAMBLINGWEBFIND PROCESS MENU           ")
        print("=" * 55)
        print("0. keywordssearch")
        print("1. keywordsindomainfetch")
        print("2. checking_url")
        print("3. capture_url")
        print("4. checking_url + capture_url")
        print("5. Exit")
        print("=" * 55)

        choice = input("Select an option (0-5): ").strip()

        if choice == "0":
            run_searxng_search()
        elif choice == "1":
            run_keywords_search()
        elif choice == "2":
            run_checking_url()
        elif choice == "3":
            run_capture_url()
        elif choice == "4":
            run_checking_url()
            run_capture_url()
        elif choice == "5" or choice.lower() in ("exit", "q", "quit"):
            print("Exiting.")
            break
        else:
            print("[!] Invalid option. Please enter 0, 1, 2, 3, 4, or 5.")


def main():
    get_db()

    parser = argparse.ArgumentParser(description="gamblingwebfind entry point")
    parser.add_argument("--seed", metavar="CSV", help="Import domains from CSV into domain_Listed and exit")
    args = parser.parse_args()

    if args.seed:
        from db.mongo_client import seed_from_csv
        seed_from_csv(args.seed)
        return

    interactive_menu()


if __name__ == "__main__":
    main()
