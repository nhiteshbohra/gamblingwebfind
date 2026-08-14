"""
checkagain.py — Re-check domains marked as 'gambling' for a specified added_date.

Re-evaluates each domain using the updated classifier (including parked & for-sale
domain detection) and updates status in MongoDB.

No Excel export and no screenshot capture.

Usage:
  python checkagain.py
  python checkagain.py --date 2026-08-14
  python checkagain.py --date 2026-08-14 --concurrency 30 --limit 100
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Silence verbose loggers to keep progress bar clean on a single line
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from db.mongo_client import (
    find_gambling_domains_by_date,
    write_result,
    get_db,
)
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify

IST = timezone(timedelta(hours=5, minutes=30))


async def recheck_gambling_domains(target_date: str, concurrency: int = None, limit: int = 0):
    """Re-check all domains currently marked 'gambling' for target_date."""
    get_db()
    concurrency = concurrency or int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    keywords = load_keywords()
    pending = list(find_gambling_domains_by_date(added_date=target_date, limit=limit))

    if not pending:
        print(f"[checkagain] No domains with status='gambling' found for added_date: '{target_date}'.")
        return None

    total_pending = len(pending)
    print(f"\n[checkagain] Found {total_pending:,} 'gambling' domains for date: {target_date}")
    print(f"[checkagain] Starting re-verification (concurrency={concurrency})...\n")

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_pending, desc="Re-verifying Gambling", unit="domain", dynamic_ncols=True)
    pbar.set_postfix({"Left": total_pending})

    stats = {
        "confirmed_gambling": 0,
        "reclassified_regular": 0,
        "reclassified_blocked": 0,
        "reclassified_dead": 0,
    }

    async def process(doc: dict):
        domain = doc["domain"]
        url = doc.get("url") or f"https://{domain}"

        async with sem:
            result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

        if result.failure_type:
            if result.failure_type == "blocked":
                status = "blocked"
                reason = ["http_403_or_blocked"]
                stats["reclassified_blocked"] += 1
            else:
                status = "dead"
                reason = [result.failure_type or "unreachable"]
                stats["reclassified_dead"] += 1
            write_result(domain, url=url, status=status, reason=reason)
        else:
            is_gambling, matched_reasons = classify(
                result.html or "", url=url, keywords=keywords, min_keywords=3
            )
            if is_gambling:
                status = "gambling"
                stats["confirmed_gambling"] += 1
            else:
                status = "regular"
                stats["reclassified_regular"] += 1

            write_result(domain, url=url, status=status, reason=matched_reasons)

        pbar.update(1)
        pbar.set_postfix({"Left": total_pending - pbar.n})

    await asyncio.gather(*[process(doc) for doc in pending])
    pbar.close()

    print("\n" + "=" * 65)
    print(f"        RE-CHECK GAMBLING RESULTS SUMMARY ({target_date})        ")
    print("=" * 65)
    print(f" Total Domains Re-verified      : {total_pending:,}")
    print(f"  * Confirmed Gambling Sites    : {stats['confirmed_gambling']:,}")
    print(f"  * Reclassified as Regular     : {stats['reclassified_regular']:,}")
    print(f"  * Reclassified as Blocked     : {stats['reclassified_blocked']:,}")
    print(f"  * Reclassified as Dead        : {stats['reclassified_dead']:,}")
    print("=" * 65)
    print("[checkagain] All updates written directly to MongoDB.")
    print("[checkagain] No Excel export or screenshot capture was run.\n")

    return stats


def main():
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Re-verify domains marked as gambling for a specific added_date")
    parser.add_argument("--date", default=None, help=f"Target added_date (YYYY-MM-DD), default: {today_str}")
    parser.add_argument("--concurrency", type=int, default=0, help="Max concurrent requests (default from .env)")
    parser.add_argument("--limit", type=int, default=0, help="Max domains to re-check (0 = all)")
    args = parser.parse_args()

    target_date = args.date
    if not target_date:
        user_input = input(f"Enter target added_date to re-check (YYYY-MM-DD) [default: {today_str}]: ").strip()
        target_date = user_input if user_input else today_str

    conc = args.concurrency or int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    asyncio.run(recheck_gambling_domains(target_date=target_date, concurrency=conc, limit=args.limit))


if __name__ == "__main__":
    main()
