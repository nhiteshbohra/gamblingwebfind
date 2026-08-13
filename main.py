"""
main.py — Single entry point for gamblingwebfind.

Config from root .env — no settings.yaml dependency.

Usage:
  python main.py
"""
import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db.mongo_client import get_db

PROJECT_ROOT = Path(__file__).resolve().parent


def run_keywords_search():
    print("\n--- Option 1: Keywords Search in Domain Fetch ---")
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

    print(f"\n[+] Starting Stage 1 Domain Fetch for keywords: {', '.join(kw_list)}")
    find_domains_script = PROJECT_ROOT / "keywordsindomainfetch" / "find_domains.py"

    cmd = [sys.executable, str(find_domains_script), "--keyword"] + kw_list
    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT / "keywordsindomainfetch"), check=True)
    except KeyboardInterrupt:
        print("\n[!] Domain fetch interrupted by user.")
    except Exception as e:
        print(f"\n[!] Error running domain fetch: {e}")


def run_checking_url():
    print("\n--- Option 2: Checking URL ---")
    print("[+] Processing unchecked URLs in DB...")
    from checking_url.runner import run as check_run
    from capture_url.excel_exporter import export_verify_workbook

    conc = int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    asyncio.run(check_run(concurrency=conc, limit=0))
    export_verify_workbook()
    print("[+] URL checking complete. verify_results.xlsx updated.")


def run_capture_url():
    print("\n--- Option 3: Capture URL ---")
    print("[+] Capturing screenshots & generating reports for gambling sites...")
    IST = timezone(timedelta(hours=5, minutes=30))
    run_ts = datetime.now(IST).strftime("%Y-%m-%d_%H-%M-%S")

    from capture_url.runner import run as capture_run
    from capture_url.excel_exporter import export_capture_workbook
    from capture_url.docx_report_generator import build_report_from_mongo

    conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    processed_ids = asyncio.run(capture_run(concurrency=conc, limit=0))

    xlsx_path = f"output/capture_results_{run_ts}.xlsx"
    docx_path = f"output/capture_report_{run_ts}.docx"

    export_capture_workbook(domain_ids=processed_ids, output_path=xlsx_path)
    build_report_from_mongo(domain_ids=processed_ids, output_path=docx_path, cleanup=True)
    print(f"[+] Capture complete. Saved Excel: {xlsx_path}, Word: {docx_path}")


def interactive_menu():
    while True:
        print("\n" + "=" * 55)
        print("           GAMBLINGWEBFIND PROCESS MENU           ")
        print("=" * 55)
        print("1. Keywords Search in Domain Fetch (Stage 1)")
        print("2. Checking URL (Stage 2)")
        print("3. Capture URL (Stage 3)")
        print("4. Both Checking & Capture URL (Stage 2 + Stage 3)")
        print("5. Exit")
        print("=" * 55)

        choice = input("Select an option (1-5): ").strip()

        if choice == "1":
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
            print("[!] Invalid option. Please enter 1, 2, 3, 4, or 5.")


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
