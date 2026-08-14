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

OUTPUT_DIR = os.path.join("output", "screenshots")


async def run(concurrency: int = None, limit: int = 0) -> list[str]:
    concurrency = concurrency or int(os.getenv("SCREENSHOT_CONCURRENCY", 15))

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

    run_captured = 0
    run_failed = 0

    async def process(doc):
        nonlocal run_captured, run_failed
        domain = doc["_id"]
        url = doc.get("url", f"https://{domain}")

        async with sem:
            path, failure_type, reason = await pool.capture_url(url, OUTPUT_DIR, retries=3)

        if path and is_valid_screenshot(path):
            run_captured += 1
            checked_domains().update_one(
                {"_id": domain},
                {"$set": {
                    "screenshot_taken": True,
                    "screenshot_failed_reason": None,
                }}
            )
        else:
            run_failed += 1
            new_status = "blocked" if failure_type == "blocked" else "dead"
            fail_reason = reason or ("HTTP 403 / Blocked" if new_status == "blocked" else "page not reachable or blank")

            # Update checked_domains with reclassified status (dead / blocked)
            checked_domains().update_one(
                {"_id": domain},
                {
                    "$set": {
                        "status": new_status,
                        "screenshot_taken": False,
                        "screenshot_failed_reason": fail_reason,
                        "exported": False,
                    },
                    "$unset": {
                        "export_status": "",
                        "exported_at": "",
                        "export_date": "",
                    }
                }
            )

            # Update source collection domain_Listed
            active_val = "blocked" if new_status == "blocked" else False
            source_domains().update_one(
                {"_id": domain},
                {"$set": {"active": active_val}}
            )
        pbar.update(1)
        pbar.set_postfix({"Left": total_pending - pbar.n})

    try:
        await asyncio.gather(*[process(doc) for doc in pending])
    finally:
        await pool.close()
        pbar.close()

    print(f"[capture] Done. Processed={len(pending)}: captured={run_captured} failed={run_failed}")
    return processed_ids
