"""
capture_url/runner.py — Screenshot gambling domains from checked_domains, write results back.
"""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

# Silence verbose loggers to keep progress bar on a single line
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import find_pending_capture, checked_domains, source_domains
from capture_url.screenshot import BrowserPool, is_valid_screenshot
from checking_url.classifier import load_keywords

OUTPUT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def run(concurrency: int = None, limit: int = 0) -> list[str]:
    concurrency = concurrency or int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    keywords = load_keywords()

    pending = list(find_pending_capture(limit=limit))
    if not pending:
        print("[capture] No gambling domains pending screenshot.")
        return []

    total_pending = len(pending)
    processed_ids = [doc["_id"] for doc in pending]
    print(f"[capture] {total_pending} domains to screenshot.")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pool = BrowserPool(concurrency=concurrency)
    await pool.start()

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_pending, desc="Capturing", unit="domain", dynamic_ncols=True)
    pbar.set_postfix({"Left": total_pending})

    stats = {
        "captured": 0,
        "regular": 0,
        "blocked": 0,
        "dead": 0,
    }

    async def process(doc):
        domain = doc["_id"]
        url = doc.get("url", f"https://{domain}")

        async with sem:
            path, failure_type, reason = await pool.capture_url(url, OUTPUT_DIR, retries=3, keywords=keywords)

        if path and is_valid_screenshot(path):
            stats["captured"] += 1
            checked_domains().update_one(
                {"_id": domain},
                {"$set": {
                    "screenshot_taken": True,
                    "screenshot_failed_reason": None,
                    "reason": reason if isinstance(reason, list) and reason else doc.get("reason", []),
                }}
            )
        elif failure_type == "regular":
            stats["regular"] += 1
            # Reclassify as regular (not gambling) — remove screenshot & export fields
            checked_domains().update_one(
                {"_id": domain},
                {
                    "$set": {
                        "status": "regular",
                        "reason": reason if isinstance(reason, list) else [],
                    },
                    "$unset": {
                        "screenshot_taken": "",
                        "screenshot_failed_reason": "",
                        "exported": "",
                        "exported_at": ""
                    }
                }
            )
            source_domains().update_one(
                {"_id": domain},
                {"$set": {"active": True}}
            )
        elif failure_type == "blocked":
            stats["blocked"] += 1
            # Reclassify as blocked (403 / Cloudflare / WAF)
            checked_domains().update_one(
                {"_id": domain},
                {
                    "$set": {
                        "status": "blocked",
                        "reason": ["http_403_or_blocked"],
                    },
                    "$unset": {
                        "screenshot_taken": "",
                        "screenshot_failed_reason": "",
                        "exported": "",
                        "exported_at": ""
                    }
                }
            )
            source_domains().update_one(
                {"_id": domain},
                {"$set": {"active": "blocked"}}
            )
        else:
            stats["dead"] += 1
            # Reclassify as dead / unreachable
            fail_reason = [str(reason)] if reason and isinstance(reason, str) else (reason if isinstance(reason, list) else ["unreachable"])
            checked_domains().update_one(
                {"_id": domain},
                {
                    "$set": {
                        "status": "dead",
                        "reason": fail_reason,
                    },
                    "$unset": {
                        "screenshot_taken": "",
                        "screenshot_failed_reason": "",
                        "exported": "",
                        "exported_at": ""
                    }
                }
            )
            source_domains().update_one(
                {"_id": domain},
                {"$set": {"active": False}}
            )

        pbar.update(1)
        pbar.set_postfix({"Left": total_pending - pbar.n})

    try:
        await asyncio.gather(*[process(doc) for doc in pending])
    finally:
        await pool.close()
        pbar.close()

    print("\n" + "=" * 60)
    print("                CAPTURE & VERIFICATION SUMMARY                ")
    print("=" * 60)
    print(f" Total Domains Checked in this run : {total_pending:,}")
    print(f"  * Screenshots Captured (Gambling): {stats['captured']:,}")
    print(f"  * Reclassified as Regular Site   : {stats['regular']:,}")
    print(f"  * Reclassified as Blocked (403)  : {stats['blocked']:,}")
    print(f"  * Reclassified as Dead / Offline : {stats['dead']:,}")
    print("=" * 60 + "\n")

    return processed_ids

