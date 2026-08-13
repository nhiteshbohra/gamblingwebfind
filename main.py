"""
main.py — Single entry point for gamblingwebfind.

Config from root .env — no settings.yaml dependency.

Usage:
  python main.py --mode check    [--concurrency N] [--limit N]
  python main.py --mode capture  [--concurrency N] [--limit N]
  python main.py --mode both     [--concurrency N] [--limit N]
  python main.py --seed FILE
"""
import argparse
import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db.mongo_client import get_db


def main():
    get_db()

    parser = argparse.ArgumentParser(description="gamblingwebfind — check / capture / both")
    parser.add_argument("--mode", choices=["check", "capture", "both"])
    parser.add_argument("--concurrency", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="Max domains to process (0 = all)")
    parser.add_argument("--seed", metavar="CSV", help="Import domains from CSV into domain_Listed and exit")
    args = parser.parse_args()

    if args.seed:
        from db.mongo_client import seed_from_csv
        seed_from_csv(args.seed)
        return

    if not args.mode:
        parser.error("--mode is required unless --seed is given")

    if args.mode in ("check", "both"):
        from checking_url.runner import run as check_run
        asyncio.run(check_run(
            concurrency=args.concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20)),
            limit=args.limit
        ))
        from reports.excel_exporter import export_verify_workbook
        export_verify_workbook()

    if args.mode in ("capture", "both"):
        IST = timezone(timedelta(hours=5, minutes=30))
        run_ts = datetime.now(IST).strftime("%Y-%m-%d_%H-%M-%S")

        from capture_url.runner import run as capture_run
        processed_ids = asyncio.run(capture_run(
            concurrency=args.concurrency or int(os.getenv("SCREENSHOT_CONCURRENCY", 15)),
            limit=args.limit
        ))
        from reports.excel_exporter import export_capture_workbook
        from reports.docx_report_generator import build_report_from_mongo
        xlsx_path = f"output/capture_results_{run_ts}.xlsx"
        docx_path = f"output/capture_report_{run_ts}.docx"
        export_capture_workbook(domain_ids=processed_ids, output_path=xlsx_path)
        build_report_from_mongo(domain_ids=processed_ids, output_path=docx_path, cleanup=True)


if __name__ == "__main__":
    main()
