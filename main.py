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

    if "," in keywords_raw:
        kw_list = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    else:
        kw_list = [k.strip() for k in keywords_raw.split() if k.strip()]
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


def ask_checking_mode() -> str:
    """Prompt user to choose whether to check new unprocessed URLs or re-check blocked URLs."""
    print("\nSelect checking target:")
    print("  1. Check new unprocessed URLs (default)")
    print("  2. Re-check blocked URLs (403 / WAF)")
    sub_choice = input("Enter choice (1-2) [default: 1]: ").strip()
    if sub_choice == "2":
        return "blocked"
    return "new"


def run_checking_url(mode: str = None):
    print("\n--- checking_url ---")
    if mode is None:
        mode = ask_checking_mode()

    from checking_url.runner import run as check_run

    concurrency = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    limit = int(os.getenv("CHECK_LIMIT", 0))

    summary = asyncio.run(check_run(concurrency=concurrency, limit=limit, mode=mode))
    if summary:
        print("[+] checking_url complete.")


def run_capture_url():
    print("\n--- capture_url ---")
    from capture_url.runner import run as capture_run
    from capture_url.excel_exporter import export_capture_workbook
    from capture_url.docx_report_generator import build_report_from_mongo

    IST = timezone(timedelta(hours=5, minutes=30))
    run_ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    limit = int(os.getenv("CAPTURE_LIMIT", 0))
    default_batch_size = int(os.getenv("EXPORT_BATCH_SIZE", 40))

    # Ask user for export preference before or during run
    print("\n" + "=" * 55)
    print("           SELECT REPORT EXPORT FORMAT           ")
    print("=" * 55)
    print("1. Single Combined Files (1 Word, 1 PDF, 1 Excel)")
    print("2. Batched Files (Split into batch folders of N items)")
    print("=" * 55)
    export_choice = input("Select export format (1-2) [default: 1]: ").strip()

    single_file = (export_choice != "2")
    batch_size = default_batch_size

    if export_choice == "2":
        bs_input = input(f"Enter batch size (e.g. 20, 40, 50) [default: {default_batch_size}]: ").strip()
        if bs_input.isdigit() and int(bs_input) > 0:
            batch_size = int(bs_input)
    else:
        batch_size = 0


    processed_ids = asyncio.run(capture_run(concurrency=conc, limit=limit))

    if not processed_ids:
        print("[+] capture_url: No gambling domains pending screenshot.")
        return

    # All output goes into a timestamped run folder: output/<timestamp>/
    run_dir = os.path.join("output", run_ts)

    if single_file:
        print(f"\n[+] Exporting {len(processed_ids)} domains to Single Combined Word, PDF & Excel -> {run_dir}/")
    else:
        print(f"\n[+] Exporting {len(processed_ids)} domains in batches of {batch_size} -> {run_dir}/")

    xlsx_result = export_capture_workbook(
        domain_ids=processed_ids,
        output_dir=run_dir,
        batch_size=batch_size,
        single_file=single_file,
    )
    report_result = build_report_from_mongo(
        domain_ids=processed_ids,
        output_dir=run_dir,
        batch_size=batch_size,
        cleanup=True,
        pdf=True,
        single_file=single_file,
    )


    print(f"\n[+] capture_url complete.")
    print(f"    Run folder  : {os.path.abspath(run_dir)}")
    if not single_file:
        print(f"    Batches     : {xlsx_result['batches']} (up to {batch_size} domains each)")
    print(f"    Captured    : {xlsx_result['captured']} | Failed: {xlsx_result['failed']}")
    print(f"    Excel files : {len([f for f in xlsx_result['files'] if f.endswith('.xlsx')])}")
    print(f"    Word files  : {len(report_result['docx_paths'])}")
    print(f"    PDF files   : {len(report_result['pdf_paths'])}")



def interactive_menu():
    while True:
        print("\n" + "=" * 55)
        print("           GAMBLINGWEBFIND PROCESS MENU           ")
        print("=" * 55)
        print("0. keywordssearch")
        print("1. keywordsindomainfetch")
        print("2. checking_url")
        print("3. capture_url")
        print("4. Exit")
        print("=" * 55)

        choice = input("Select an option (0-4): ").strip()

        if choice == "0":
            run_searxng_search()
        elif choice == "1":
            run_keywords_search()
        elif choice == "2":
            run_checking_url()
        elif choice == "3":
            run_capture_url()
        elif choice == "4" or choice.lower() in ("exit", "q", "quit"):
            print("Exiting.")
            break
        else:
            print("[!] Invalid option. Please enter 0, 1, 2, 3, or 4.")


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
