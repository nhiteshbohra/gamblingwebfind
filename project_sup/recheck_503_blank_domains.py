"""
project_sup/recheck_503_blank_domains.py
─────────────────────────────────────────
Specifically re-checks all domains in MongoDB marked as:
  status='dead' AND reason='Dead: 503 / Blank unrendered screenshot'

Workflow:
  • Visits each domain with Playwright.
  • If the site is NOW ALIVE with a REAL screenshot:
      - Saves the valid screenshot to output/screenshots/
      - Updates MongoDB:
          checked_domains: status='gambling', screenshot_taken=True, screenshot_date=YYYY-MM-DD
          domain_Listed  : active=True, processed=True
  • If the site STILL returns a 503 error, Cloudflare error, or blank page:
      - Saves the error screenshot into output/503error/ (for user reference)
      - Keeps MongoDB status='dead', screenshot_taken=False
  • If the site is unreachable (DNS fail / Timeout):
      - Keeps MongoDB status='dead'

Usage:
    python project_sup/recheck_503_blank_domains.py
    python project_sup/recheck_503_blank_domains.py --concurrency 10 --limit 50
"""

import os
import sys
import io
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageStat
from dotenv import load_dotenv
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, get_db, IST
from export_domains.screenshot import (
    BrowserPool,
    is_valid_screenshot,
    _url_to_filename,
    delete_screenshot,
)

SCREENSHOTS_DIR = os.getenv("SCREENSHOT_DIR", str(PROJECT_ROOT / "output" / "screenshots"))
ERROR_503_DIR   = str(PROJECT_ROOT / "output" / "503error")


async def recheck_domains(concurrency: int = 8, limit: int = 0):
    get_db()

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(ERROR_503_DIR, exist_ok=True)

    today_date = datetime.now(IST).strftime("%Y-%m-%d")

    # 1. Fetch targeted 503 / blank domains from MongoDB
    query = {"status": "dead", "reason": "Dead: 503 / Blank unrendered screenshot"}
    cursor = checked_domains().find(query, {"_id": 1, "domain": 1, "url": 1})
    if limit > 0:
        cursor = cursor.limit(limit)

    targets = list(cursor)
    total_targets = len(targets)

    print(f"\n[+] Found {total_targets:,} domains marked as 'Dead: 503 / Blank unrendered screenshot'.")
    if total_targets == 0:
        print("[+] Nothing to recheck.")
        return

    print(f"[+] Output for recovered live sites : {SCREENSHOTS_DIR}")
    print(f"[+] Output for persistent 503 errors: {ERROR_503_DIR}")
    print(f"[+] Starting browser pool with concurrency={concurrency}...\n")

    browser_pool = BrowserPool(concurrency=min(concurrency, 8))
    await browser_pool.start()

    stats = {
        "recovered_live": 0,
        "still_503_error": 0,
        "unreachable_dead": 0,
    }

    lock = asyncio.Lock()
    pbar = tqdm(total=total_targets, desc="Re-checking 503/Blank", unit="domain", dynamic_ncols=True)

    async def process_item(item: dict):
        domain = item.get("domain") or item.get("_id")
        url = item.get("url") or f"https://{domain}"
        clean_dom = domain.removeprefix("www.")
        filename = _url_to_filename(url)

        valid_dest = os.path.join(SCREENSHOTS_DIR, filename)
        error_dest = os.path.join(ERROR_503_DIR, filename)

        try:
            # Capture with Playwright
            ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                url, SCREENSHOTS_DIR, retries=2, keywords=None
            )

            if ss_path and is_valid_screenshot(ss_path):
                # Verify that the screenshot is not a parked / Dynadot / placeholder lander
                from checking_url.classifier import is_parked_or_for_sale
                # Check HTML/text if available or keep safe
                checked_domains().update_one(
                    {"_id": domain},
                    {
                        "$set": {
                            "status": "gambling",
                            "reason": "Known gambling — re-verified active",
                            "screenshot_taken": True,
                            "screenshot_date": today_date,
                        },
                        "$unset": {
                            "screenshot_failed_reason": "",
                        }
                    }
                )
                source_domains().update_one(
                    {"_id": domain},
                    {"$set": {"active": True, "processed": True}}
                )
                async with lock:
                    stats["recovered_live"] += 1

            else:
                # ── CASE 2: Capture returned 503, WAF, or blank page ──
                delete_screenshot(clean_dom, SCREENSHOTS_DIR)

                _DEAD_SS = {"dead", "dns_failed", "connection_refused", "timeout", "ssl_or_reset"}
                if ss_status in _DEAD_SS:
                    # Unreachable / Offline
                    checked_domains().update_one(
                        {"_id": domain},
                        {
                            "$set": {
                                "status": "dead",
                                "reason": f"Dead: {ss_reason or 'Unreachable'}",
                            },
                            "$unset": {
                                "screenshot_taken": "",
                                "screenshot_date": "",
                                "screenshot_failed_reason": "",
                                "exported": "",
                                "exported_at": "",
                            }
                        }
                    )
                    async with lock:
                        stats["unreachable_dead"] += 1
                else:
                    # Still 503 error / WAF / unrenderable page
                    checked_domains().update_one(
                        {"_id": domain},
                        {
                            "$set": {
                                "status": "dead",
                                "reason": "Dead: 503 / Blank unrendered screenshot",
                            },
                            "$unset": {
                                "screenshot_taken": "",
                                "screenshot_date": "",
                                "screenshot_failed_reason": "",
                                "exported": "",
                                "exported_at": "",
                            }
                        }
                    )
                    async with lock:
                        stats["still_503_error"] += 1

        except Exception as e:
            async with lock:
                stats["unreachable_dead"] += 1

        finally:
            async with lock:
                pbar.update(1)
                pbar.set_postfix({
                    "Recovered": stats["recovered_live"],
                    "503 Error": stats["still_503_error"],
                    "Dead": stats["unreachable_dead"],
                })

    queue: asyncio.Queue = asyncio.Queue()
    for item in targets:
        queue.put_nowait(item)

    async def worker():
        while not queue.empty():
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process_item(item)
            finally:
                queue.task_done()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n[+] Stopped by user.")
    finally:
        await browser_pool.close()
        pbar.close()

    # 3. Final Summary
    print("\n" + "=" * 65)
    print("       503 & BLANK DOMAIN RE-CHECK SCAN SUMMARY")
    print("=" * 65)
    print(f"  Total Domains Re-checked       : {total_targets:,}")
    print(f"  Recovered (Valid & Alive)      : {stats['recovered_live']:,} -> output/screenshots/")
    print(f"  Still 503 / Blank Error        : {stats['still_503_error']:,} -> output/503error/")
    print(f"  Dead / DNS / Timeout           : {stats['unreachable_dead']:,}")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Re-check domains marked as 503/Blank unrendered")
    parser.add_argument("--concurrency", "-c", type=int, default=8, help="Number of concurrent browser workers")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Limit number of domains to re-check (0 for all)")
    args = parser.parse_args()

    asyncio.run(recheck_domains(concurrency=args.concurrency, limit=args.limit))


if __name__ == "__main__":
    main()
