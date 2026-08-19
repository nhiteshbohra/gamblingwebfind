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
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db.mongo_client import get_db

PROJECT_ROOT = Path(__file__).resolve().parent


def _is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_web_dashboard(host: str = "127.0.0.1", port: int = 8081):
    url = f"http://{host}:{port}"
    if _is_port_in_use(host, port):
        print(f"[+] Web Dashboard: Already running on {url}")
        webbrowser.open(url)
        return

    def _serve():
        import uvicorn
        from api.main import app
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        server.run()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    for _ in range(30):
        if _is_port_in_use(host, port):
            break
        time.sleep(0.1)

    print(f"[+] Web Dashboard: Started at {url}")
    webbrowser.open(url)

def _get_keywords_file() -> Path:
    candidates = list(PROJECT_ROOT.glob("gambling_top_*_keywords.json")) + list(PROJECT_ROOT.glob("*keyword*.json"))
    return candidates[0] if candidates else (PROJECT_ROOT / "gambling_top_944_keywords.json")


def _load_keywords() -> tuple[list[str], str]:
    """Load all keywords from dynamic keywords json file."""
    kw_path = _get_keywords_file()
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            terms = json.load(f)
        return [str(k).strip().lower() for k in terms if str(k).strip()], kw_path.name
    return [], kw_path.name


def run_searxng_search():
    print("\n--- keywordssearch ---")

    keywords, kw_name = _load_keywords()
    print(f"[+] Loaded {len(keywords)} keywords from {kw_name}")
    if keywords:
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
    """Prompt user to choose target domain queue for checking/re-checking."""
    print("\nSelect checking target queue:")
    print("  0. Back to main menu")
    print("  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]")
    print("  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)")
    print("  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)")
    print("  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)")
    print("  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)")
    sub_choice = input("\nEnter choice (0-5) [default: 1]: ").strip()
    if sub_choice in ("0", "b", "back"):
        return None
    if sub_choice == "2":
        return "blocked"
    if sub_choice == "3":
        return "unconfirmed"
    if sub_choice == "4":
        return "regular"
    if sub_choice == "5":
        return "dead"
    return "new"


def run_checking_url(mode: str = None):
    print("\n--- checking_url (Fetch, AI Classify & Capture Screenshots) ---")
    if mode is None:
        mode = ask_checking_mode()
    if mode is None:
        return  # user chose back

    from checking_url.ai_classifier import start_ollama_if_needed
    try:
        asyncio.run(start_ollama_if_needed())
    except Exception as e:
        print(f"[!] Local AI status check error: {e}")

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
    from export_domains.screenshot import all_filename_candidates
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
        candidates = all_filename_candidates(url, domain)
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


def run_export_domains():
    print("\n--- export_domains (Export & Batch Splitting) ---")
    print("  0. Back to main menu")
    print("  1. Export from Database  (Generate Word, PDF & Excel from MongoDB)")
    print("  2. Divide into Batches   (Split existing Excel/CSV + PDF into batch folders)")
    mode = input("Select option (0-2) [default: 1]: ").strip()
    if mode in ("0", "b", "back"):
        return

    if mode == "2":
        from export_domains.batch_splitter import prompt_divide_into_batches
        prompt_divide_into_batches()
        return

    # Mode 1: Export from Database
    from export_domains.exporter import run as export_run

    limit = int(os.getenv("CAPTURE_LIMIT", 0))
    result = asyncio.run(export_run(limit=limit))

    if not result or (result.get("captured", 0) == 0 and result.get("failed", 0) == 0):
        print("[+] export_domains: No unexported gambling domains found.")
        return

    print(f"\n[+] Export Complete:")
    print(f"    Run ID   : {result.get('run_id')}")
    print(f"    PDF      : {result.get('pdf') or 'N/A'}")
    print(f"    Excel    : {result.get('xlsx') or 'N/A'}")
    print(f"    Captured : {result.get('captured')}")
    print(f"    Failed   : {result.get('failed')}")

    if result.get("pdf") and result.get("xlsx") and os.path.exists(result["pdf"]):
        try:
            ask_split = input("\n[?] Would you like to divide these exported files into batches now? (y/n) [default: n]: ").strip().lower()
            if ask_split in ("y", "yes"):
                from export_domains.batch_splitter import create_batches
                create_batches(csv_path=result["xlsx"], pdf_path=result["pdf"])
        except Exception as e:
            print(f"[!] Batch splitting error: {e}")




def interactive_menu():
    while True:
        print("\n" + "=" * 58)
        print("             GAMBLINGWEBFIND PROCESS MENU             ")
        print("=" * 58)
        print("1. keywordssearch        (SearXNG / Multi-Engine Search)")
        print("2. checking_url          (Fetch, AI Classify & Screenshot)")
        print("3. export_domains        (Export Reports & Divide into Batches)")
        print("0. Exit")
        print("=" * 58)

        choice = input("Select an option (1-3, 0 to exit): ").strip()

        if choice == "1":
            run_searxng_search()
        elif choice == "2":
            run_checking_url()
        elif choice == "3":
            run_export_domains()
        elif choice == "0" or choice.lower() in ("exit", "q", "quit"):
            print("Exiting.")
            break
        else:
            print("[!] Invalid option. Please enter 1-3 or 0 to exit.")


def main():
    parser = argparse.ArgumentParser(description="gamblingwebfind entry point")
    parser.add_argument("--seed", metavar="CSV", help="Import domains from CSV into domain_Listed and exit")
    parser.add_argument("--no-ui", action="store_true", help="Do not start or open the web dashboard")
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or 8081), help="Web dashboard port")
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST") or os.getenv("HOST") or "127.0.0.1", help="Web dashboard host")
    args = parser.parse_args()

    if args.seed:
        from db.mongo_client import seed_from_csv
        seed_from_csv(args.seed)
        return

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

    if not args.no_ui:
        start_web_dashboard(host=args.host, port=args.port)

    interactive_menu()


if __name__ == "__main__":
    main()
