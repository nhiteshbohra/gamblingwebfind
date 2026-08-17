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
from checking_url.ai_classifier import check_ollama_status

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


def ask_checking_mode() -> str | None:
    """Prompt user to choose whether to check new unprocessed URLs, re-check blocked URLs, or re-check unconfirmed URLs."""
    print("\nSelect checking target:")
    print("  0. Back to main menu")
    print("  1. Check new unprocessed URLs (default)")
    print("  2. Re-check blocked URLs (403 / WAF)")
    print("  3. Re-check unconfirmed URLs (Ollama AI re-evaluation)")
    sub_choice = input("Enter choice (0-3) [default: 1]: ").strip()
    if sub_choice in ("0", "b", "back"):
        return None
    if sub_choice == "2":
        return "blocked"
    if sub_choice == "3":
        return "unconfirmed"
    return "new"


def run_checking_url(mode: str = None):
    print("\n--- checking_url (Fetch, AI Classify & Capture Screenshots) ---")
    if mode is None:
        mode = ask_checking_mode()
    if mode is None:
        return  # user chose back

    from checking_url.runner import run as check_run

    concurrency = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    limit = int(os.getenv("CHECK_LIMIT", 0))

    summary = asyncio.run(check_run(concurrency=concurrency, limit=limit, mode=mode))
    if summary:
        print("[+] checking_url complete.")


def _size_based_split(domain_ids: list, pdf_limit_mb: float = 24.0) -> list[list[str]]:
    """Split domain_ids into batches where each batch's estimated PDF size stays under pdf_limit_mb.

    Estimation: JPEG file size on disk + PDF_PAGE_OVERHEAD_BYTES per entry.
    Target ceiling is set slightly below the limit to leave headroom.
    """
    from capture_url.screenshot import _url_to_filename
    SCREENSHOTS_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))
    PDF_PAGE_OVERHEAD = 12_000          # ~12KB per page for text/metadata
    PDF_LIMIT_BYTES   = int(pdf_limit_mb * 1024 * 1024 * 0.92)  # 92% of 24MB as safe ceiling

    batches = []
    current_batch = []
    current_size  = 0

    from db.mongo_client import checked_domains as _cd
    for domain in domain_ids:
        doc = _cd().find_one({"_id": domain}, {"url": 1})
        url = (doc or {}).get("url") or f"https://{domain}"
        candidates = [
            _url_to_filename(url),
            _url_to_filename(f"https://{domain}"),
            _url_to_filename(f"http://{domain}"),
        ]
        jpeg_size = 0
        for cand in candidates:
            p = os.path.join(SCREENSHOTS_DIR, cand)
            if os.path.exists(p):
                jpeg_size = os.path.getsize(p)
                break

        entry_size = jpeg_size + PDF_PAGE_OVERHEAD

        if current_batch and (current_size + entry_size) > PDF_LIMIT_BYTES:
            batches.append(current_batch)
            current_batch = []
            current_size  = 0

        current_batch.append(domain)
        current_size += entry_size

    if current_batch:
        batches.append(current_batch)

    return batches


def run_capture_url():
    print("\n--- capture_url (Export Reports: Word, PDF & Excel) ---")
    from capture_url.runner import run as capture_run
    from capture_url.excel_exporter import export_capture_workbook
    from capture_url.docx_report_generator import build_report_from_mongo

    IST = timezone(timedelta(hours=5, minutes=30))
    run_ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    limit = int(os.getenv("CAPTURE_LIMIT", 0))
    default_batch_size = int(os.getenv("EXPORT_BATCH_SIZE", 40))

    # Ask user for export preference before run
    print("\n" + "=" * 55)
    print("           SELECT REPORT EXPORT FORMAT           ")
    print("=" * 55)
    print("0. Back to main menu")
    print("1. Single Combined Files (1 Word, 1 PDF, 1 Excel)")
    print("2. Batched Files (Split into batch folders of N items)")
    print("3. Batched Files (Split by PDF size — 24MB limit, PDF + Excel only)")
    print("=" * 55)
    export_choice = input("Select export format (0-3) [default: 1]: ").strip()
    if export_choice in ("0", "b", "back"):
        return

    processed_ids = asyncio.run(capture_run(concurrency=conc, limit=limit))

    if not processed_ids:
        print("[+] capture_url: No gambling domains pending report export.")
        return

    run_dir = os.path.join("output", run_ts)

    # ── Option 3: Size-based PDF batching, no Word ────────────────────────────
    if export_choice == "3":
        print(f"\n[+] Size-based export: splitting {len(processed_ids)} domains into ≤24MB PDF batches...")
        size_batches = _size_based_split(processed_ids, pdf_limit_mb=24.0)
        total_pdfs = 0
        total_xlsx = 0
        for batch_num, batch_ids in enumerate(size_batches, 1):
            batch_label = f"batch_{batch_num:03d}"
            batch_dir = os.path.join(run_dir, batch_label)
            print(f"\n[+] {batch_label}: {len(batch_ids)} domains -> {batch_dir}/")

            # PDF only (no Word)
            report_result = build_report_from_mongo(
                domain_ids=batch_ids,
                output_dir=batch_dir,
                batch_size=0,           # single file per batch
                cleanup=False,
                pdf=True,
                single_file=True,
            )
            total_pdfs += len(report_result["pdf_paths"])

            # Paired Excel
            xlsx_result = export_capture_workbook(
                domain_ids=batch_ids,
                output_dir=batch_dir,
                batch_size=0,
                single_file=True,
            )
            total_xlsx += len([f for f in xlsx_result["files"] if f.endswith(".xlsx")])

        print(f"\n[+] Size-based export complete.")
        print(f"    Run folder  : {os.path.abspath(run_dir)}")
        print(f"    Batches     : {len(size_batches)}")
        print(f"    PDF files   : {total_pdfs}")
        print(f"    Excel files : {total_xlsx}")
        return

    # ── Options 1 & 2: original behaviour ────────────────────────────────────
    single_file = (export_choice != "2")
    batch_size = default_batch_size

    if export_choice == "2":
        bs_input = input(f"Enter batch size (e.g. 20, 40, 50) [0 to go back, default: {default_batch_size}]: ").strip()
        if bs_input in ("0", "b", "back"):
            return
        if bs_input.isdigit() and int(bs_input) > 0:
            batch_size = int(bs_input)
    else:
        batch_size = 0

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
        cleanup=False,
        pdf=True,
        single_file=single_file,
    )

    print(f"\n[+] Export complete.")
    print(f"    Run folder  : {os.path.abspath(run_dir)}")
    if not single_file:
        print(f"    Batches     : {xlsx_result['batches']} (up to {batch_size} domains each)")
    print(f"    Captured    : {xlsx_result['captured']} | Failed: {xlsx_result['failed']}")
    print(f"    Excel files : {len([f for f in xlsx_result['files'] if f.endswith('.xlsx')])}")
    print(f"    Word files  : {len(report_result['docx_paths'])}")
    print(f"    PDF files   : {len(report_result['pdf_paths'])}")



def run_import_true_positives():
    print("\n--- import_true_positives (Import Confirmed Gambling URLs from File) ---")
    file_path = input("Enter path to Excel (.xlsx/.xls), CSV, or .txt file with gambling URLs (0 to go back): ").strip().strip('"')
    if file_path in ("0", "b", "back") or not file_path:
        return
    if not os.path.exists(file_path):
        print(f"[!] File not found: {file_path}")
        return
    from db.mongo_client import ingest_true_positives
    inserted, skipped = ingest_true_positives(file_path)
    print(f"[+] Import complete. New: {inserted} | Reset for re-capture: {skipped}")
    if inserted + skipped > 0:
        print(f"    Run Option 5 to capture screenshots, then Option 3 to export reports.")


def run_screenshot_only():
    print("\n--- screenshot_only (Capture Missing Screenshots — No Re-classification) ---")
    print("  0. Back to main menu")
    print("  1. From file     (Excel .xlsx/.xls, CSV, or .txt list of domains/URLs)")
    print("  2. From database (all status=gambling, screenshot_taken=False)")
    mode = input("Select mode (0-2) [default: 2]: ").strip()
    if mode in ("0", "b", "back"):
        return

    from capture_url.screenshot_runner import run as ss_run
    concurrency = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))

    if mode == "1":
        file_path = input("Enter path to file with domains/URLs (0 to go back): ").strip().strip('"')
        if file_path in ("0", "b", "back") or not file_path:
            return
        if not os.path.exists(file_path):
            print(f"[!] File not found: {file_path}")
            return

        from db.mongo_client import extract_domains_from_file
        domain_ids = extract_domains_from_file(file_path)

        if not domain_ids:
            print("[!] No valid domains found in file.")
            return

        print(f"[+] {len(domain_ids)} domain(s) read from '{os.path.basename(file_path)}'.")
        result = asyncio.run(ss_run(domain_ids=domain_ids, concurrency=concurrency))

    else:
        # Mode 2: query entire collection for pending screenshots
        limit = int(os.getenv("CAPTURE_LIMIT", 0))
        result = asyncio.run(ss_run(domain_ids=None, concurrency=concurrency, limit=limit))

    print(f"[+] Done. Captured: {result['captured']} | Failed: {result['failed']} | Total: {result['total']}")
    if result.get("excel_path"):
        print(f"    Excel Summary : {os.path.abspath(result['excel_path'])}")





def interactive_menu():
    while True:
        print("\n" + "=" * 58)
        print("             GAMBLINGWEBFIND PROCESS MENU             ")
        print("=" * 58)
        print("1. keywordssearch        (SearXNG / Multi-Engine Search)")
        print("2. checking_url          (Fetch, AI Classify & Screenshot)")
        print("3. capture_url           (Export Word, PDF & Excel Reports)")
        print("4. import_true_positives (Import Confirmed Gambling URLs from File)")
        print("5. screenshot_only       (Capture Missing Screenshots — No Re-classification)")
        print("0. Exit")
        print("=" * 58)

        choice = input("Select an option (1-5, 0 to exit): ").strip()

        if choice == "1":
            run_searxng_search()
        elif choice == "2":
            run_checking_url()
        elif choice == "3":
            run_capture_url()
        elif choice == "4":
            run_import_true_positives()
        elif choice == "5":
            run_screenshot_only()
        elif choice == "0" or choice.lower() in ("exit", "q", "quit"):
            print("Exiting.")
            break
        else:
            print("[!] Invalid option. Please enter 1-5 or 0 to exit.")


def main():
    from pymongo.errors import ServerSelectionTimeoutError
    db_name = os.getenv("MONGO_DB_NAME", "gamblingsites")
    try:
        get_db().command("ping")
        print(f"[+] MongoDB: Connected (Database: '{db_name}')")
    except ServerSelectionTimeoutError:
        print(f"[!] Cannot connect to MongoDB — please start MongoDB and try again.")
        print("    Default URI: mongodb://localhost:27017/")
        sys.exit(1)
    except Exception as e:
        print(f"[!] MongoDB error: {e}")
        sys.exit(1)

    # Check Ollama AI status
    try:
        ollama_ok, ollama_msg = asyncio.run(check_ollama_status())
        if ollama_ok:
            print(f"[+] Local AI: {ollama_msg}")
        else:
            print(f"[!] Local AI Warning: {ollama_msg}")
            print("    (If Ollama is offline, 1-2 keyword sites will be marked 'unconfirmed' to re-run later)")
    except Exception as e:
        print(f"[!] Local AI status check error: {e}")

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
