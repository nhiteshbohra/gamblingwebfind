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


def ask_checking_mode() -> tuple[str | None, bool | None]:
    """Prompt user to choose target domain queue for checking/re-checking and speed mode."""
    print("\nSelect checking target queue:")
    print("  0. Back to main menu")
    print("  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]")
    print("  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)")
    print("  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)")
    print("  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)")
    print("  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)")
    print("  6. Re-check For-Sale Sites     (Re-test parked/registrar landers in case they've gone live)")
    sub_choice = input("\nEnter choice (0-6) [default: 1]: ").strip()
    if sub_choice in ("0", "b", "back"):
        return None, None
    mode_map = {
        "2": "blocked",
        "3": "unconfirmed",
        "4": "regular",
        "5": "dead",
        "6": "for_sale",
    }
    mode = mode_map.get(sub_choice, "new")

    # Screening mode selection: Fast Bulk (heuristics, no Ollama) vs Deep AI
    print("\nSelect screening mode:")
    if mode == "unconfirmed":
        print("  1. Deep AI Inspection  (Ollama AI 2-Round Challenge) [default for unconfirmed]")
        print("  2. Fast Bulk Scan      (High-Speed Heuristics only, 0 Ollama calls)")
        sp_choice = input("Enter choice (1-2) [default: 1]: ").strip()
        fast_bulk = (sp_choice == "2")
    else:
        print("  1. Fast Bulk Scan      (High-Speed Heuristics, 0 Ollama calls — 30-50 domains/sec, avoids freeze) [default]")
        print("  2. Deep AI Inspection  (Ollama Dual-Model Challenge + Playwright fallback — for small batches)")
        sp_choice = input("Enter choice (1-2) [default: 1]: ").strip()
        fast_bulk = (sp_choice != "2")

    return mode, fast_bulk


def run_checking_url(mode: str = None, fast_bulk: bool = None):
    print("\n--- checking_url (Fetch, AI Classify & Capture Screenshots) ---")
    if mode is None:
        mode, fast_bulk = ask_checking_mode()
    if mode is None:
        return  # user chose back

    if fast_bulk is None:
        fast_bulk = os.getenv("FAST_BULK_MODE", "false").lower() in ("true", "1", "yes")

    if not fast_bulk:
        from checking_url.ai_classifier import start_ollama_if_needed
        try:
            asyncio.run(start_ollama_if_needed())
        except Exception as e:
            print(f"[!] Local AI status check error: {e}")
    else:
        print("[+] Fast Bulk Screening: Ollama kept offline to protect system resources.")

    from checking_url.runner import run as check_run

    default_concurrency = 30 if fast_bulk else 10
    concurrency = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", default_concurrency)))
    limit = int(os.getenv("CHECK_LIMIT", 0))
    min_age_days = int(os.getenv("RECHECK_MIN_AGE_DAYS", 0))

    # ponytail: Clean Ctrl+C exit without raw Python traceback
    try:
        summary = asyncio.run(check_run(concurrency=concurrency, limit=limit, mode=mode, min_age_days=min_age_days, fast_bulk=fast_bulk))
        if summary:
            print("[+] checking_url complete.")
    except KeyboardInterrupt:
        print("\n[+] Stopped by user. Progress saved — you can resume anytime.")


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
    print("\n--- export_domains (Ultra-Fast Direct Batch Export) ---")
    print("  0. Back to main menu")
    print("  1. Export Directly to Batches (Fast PyMuPDF PDF + Excel Batches, <=24 MB) [default]")
    print("  2. Divide Existing Files into Batches (Split legacy Excel/CSV + PDF)")
    mode = input("Select option (0-2) [default: 1]: ").strip()
    if mode in ("0", "b", "back"):
        return

    if mode == "2":
        from export_domains.batch_splitter import prompt_divide_into_batches
        prompt_divide_into_batches()
        return

    # Mode 1: Export directly to batches from Database
    from export_domains.exporter import run as export_run

    limit = int(os.getenv("CAPTURE_LIMIT", 0))
    result = asyncio.run(export_run(limit=limit))

    if not result or (result.get("captured", 0) == 0 and result.get("failed", 0) == 0):
        print("[+] export_domains: No unexported gambling domains found.")
        return

    print(f"\n[+] Export Complete:")
    print(f"    Run ID       : {result.get('run_id')}")
    print(f"    Batches      : {result.get('batches', 0)} batch folders in '{result.get('batches_dir')}'")
    print(f"    Sample PDF   : {result.get('pdf') or 'N/A'}")
    print(f"    Master Excel : {result.get('xlsx') or 'N/A'}")
    print(f"    Captured     : {result.get('captured')}")
    print(f"    Failed       : {result.get('failed')}")






def run_known_gambling_scan():
    print("\n--- Known Gambling Domains (Import, Liveness Check & Screenshot) ---")
    print("    Enter the full path to your domains file (.txt / .csv / .xlsx).")
    print("    Type 'back' or leave empty to return to the main menu.\n")

    # Keep asking until a valid file is given or user cancels
    while True:
        user_path = input("File path: ").strip().strip('"').strip("'")
        if not user_path or user_path.lower() in ("back", "b", "0"):
            print("[+] Cancelled.")
            return

        domains_file = Path(user_path)
        if domains_file.exists() and domains_file.is_file():
            break
        print(f"[!] File not found: {user_path}")
        print("    Please check the path and try again (or type 'back' to cancel).")

    # Derive a checkpoint name from the file stem so separate files
    # don't overwrite each other's progress.
    checkpoint_name = f"known_gambling_progress_{domains_file.stem}.json"
    checkpoint_path = str(PROJECT_ROOT / "output" / checkpoint_name)
    print(f"\n[+] File          : {domains_file}")
    print(f"[+] Checkpoint    : {checkpoint_path}")
    print("    (Progress is auto-saved every 25 domains — safe to Ctrl+C and resume anytime.)")

    concurrency = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 10)))
    confirm = input(f"\n[?] Start scan with concurrency={concurrency}? (y/n) [default: y]: ").strip().lower()
    if confirm in ("n", "no"):
        print("[+] Aborted.")
        return

    from checking_url.known_gambling_runner import run as known_run
    try:
        asyncio.run(known_run(
            domains_file=str(domains_file),
            concurrency=concurrency,
            checkpoint_path=checkpoint_path,
        ))
    except KeyboardInterrupt:
        print("\n[+] Stopped by user. Progress saved — run again to resume.")



def run_reported_blocked_checker():
    import checking_url.reported_blocked_checker as rbc

    print("\n--- Recheck Reported/Exported Gambling Domains (ISP/Regulator Block Status) ---")
    print("    Re-fetches every exported 'gambling' domain to see if it's gone unreachable")
    print("    since being reported.")
    print(f"    Needs {rbc.CONFIRM_ATTEMPTS} failed attempts {rbc.CONFIRM_DELAY_SECONDS:.0f}s apart to count as down at all --")
    print("    rules out a single transient blip. Confirmed down, it's marked")
    print("    'reported_down'. Found reachable again on a later run, it reverts")
    print("    back to 'gambling'. Run this whenever you want -- it's on-demand, not automatic.\n")

    confirm = input("[?] Start recheck now? (y/n) [default: y]: ").strip().lower()
    if confirm in ("n", "no"):
        print("[+] Cancelled.")
        return

    concurrency = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    try:
        asyncio.run(rbc.run(concurrency=concurrency))
    except KeyboardInterrupt:
        print("\n[+] Stopped by user. Already-checked domains were saved — run again to continue.")


def run_list_fetcher():
    from list_fetcher.keysfetch_from_txt import import_blocklists_to_mongo
    print("\n--- list_fetcher (Import Blocklists into domain_Listed Queue) ---")
    print("    Fetches curated external gambling blocklists from sources.txt")
    print("    and queues them into MongoDB collection domain_Listed.")
    print("    After importing, select Option 2 (checking_url) to verify and screenshot them.\n")

    confirm = input("[?] Start blocklist import now? (y/n) [default: y]: ").strip().lower()
    if confirm in ("n", "no"):
        print("[+] Cancelled.")
        return

    try:
        import_blocklists_to_mongo()
    except KeyboardInterrupt:
        print("\n[+] Stopped by user.")


def interactive_menu():
    while True:
        print("\n" + "=" * 65)
        print("              GAMBLINGWEBFIND PROCESS MENU              ")
        print("=" * 65)
        print("1. keywordssearch        (SearXNG / Multi-Engine Search)")
        print("2. checking_url          (Fetch, AI Classify & Screenshot)")
        print("3. export_domains        (Export Reports & Divide into Batches)")
        print("4. known gambling scan   (Import list, Screenshot live, Mark dead)")
        print("5. recheck reported      (Are exported/reported domains blocked yet?)")
        print("6. list_fetcher          (Import Blocklists into domain_Listed Queue)")
        print("0. Exit")
        print("=" * 65)

        choice = input("Select an option (1-6, 0 to exit): ").strip()

        if choice == "1":
            run_searxng_search()
        elif choice == "2":
            run_checking_url()
        elif choice == "3":
            run_export_domains()
        elif choice == "4":
            run_known_gambling_scan()
        elif choice == "5":
            run_reported_blocked_checker()
        elif choice == "6":
            run_list_fetcher()
        elif choice == "0" or choice.lower() in ("exit", "q", "quit"):
            print("Exiting.")
            break
        else:
            print("[!] Invalid option. Please enter 1-6 or 0 to exit.")


def shutdown_background_services():
    """Safely stop SearXNG Docker container and unload Ollama AI models on exit."""
    print("\n[+] Safely shutting down background services and releasing system RAM...")
    try:
        from keywordssearch.searxng_search import stop_searxng_docker
        stop_searxng_docker()
    except Exception:
        pass
    try:
        from checking_url.ai_classifier import stop_ollama_if_running
        asyncio.run(stop_ollama_if_running())
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="gamblingwebfind entry point")
    parser.add_argument("--seed", metavar="CSV", help="Import domains from CSV into domain_Listed and exit")
    parser.add_argument("--fetch-blocklists", action="store_true", help="Fetch and queue blocklists into domain_Listed and exit")
    parser.add_argument("--no-ui", action="store_true", help="Do not start or open the web dashboard")
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or 8081), help="Web dashboard port")
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST") or os.getenv("HOST") or "127.0.0.1", help="Web dashboard host")
    args = parser.parse_args()

    if args.seed:
        from db.mongo_client import seed_from_csv
        seed_from_csv(args.seed)
        return

    if args.fetch_blocklists:
        from list_fetcher.keysfetch_from_txt import import_blocklists_to_mongo
        import_blocklists_to_mongo()
        return

    from pymongo.errors import ServerSelectionTimeoutError
    db = get_db()
    db_name = db.name if db is not None else os.getenv("MONGO_DB_NAME", "")
    target_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    try:
        db.command("ping")
        print(f"[+] MongoDB: Connected (Database: '{db_name}')")
    except ServerSelectionTimeoutError:
        print(f"[!] Cannot connect to MongoDB — please start MongoDB and try again.")
        print(f"    Target URI: {target_uri}")
        sys.exit(1)
    except Exception as e:
        print(f"[!] MongoDB error: {e}")
        sys.exit(1)

    if not args.no_ui:
        start_web_dashboard(host=args.host, port=args.port)

    try:
        interactive_menu()
    finally:
        shutdown_background_services()


if __name__ == "__main__":
    main()
