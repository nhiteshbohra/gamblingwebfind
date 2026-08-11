"""
capture_url/runner.py — Screenshot gambling domains from checked_domains, write results back.
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import find_pending_capture, checked_domains
from capture_url.screenshot import BrowserPool, is_valid_screenshot

OUTPUT_DIR = os.path.join("output", "screenshots")


async def run(concurrency: int = None, limit: int = 0):
    concurrency = concurrency or int(os.getenv("SCREENSHOT_CONCURRENCY", 15))

    pending = list(find_pending_capture(limit=limit))
    if not pending:
        print("[capture] No gambling domains pending screenshot.")
        return

    print(f"[capture] {len(pending)} domains to screenshot.")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pool = BrowserPool(concurrency=concurrency)
    await pool.start()

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=len(pending), desc="Capturing", unit="domain", dynamic_ncols=True)

    async def process(doc):
        domain = doc["_id"]
        url = doc.get("url", f"https://{domain}")
        now = datetime.now(timezone.utc).isoformat()

        async with sem:
            path = await pool.capture_url(url, OUTPUT_DIR, retries=3)

        if path and is_valid_screenshot(path):
            checked_domains().update_one(
                {"_id": domain},
                {"$set": {
                    "screenshot_taken": True,
                    "screenshot_failed_reason": None,
                }}
            )
        else:
            checked_domains().update_one(
                {"_id": domain},
                {"$set": {
                    "screenshot_taken": False,
                    "screenshot_failed_reason": "timeout or blank",
                }}
            )
        pbar.update(1)

    try:
        await asyncio.gather(*[process(doc) for doc in pending])
    finally:
        await pool.close()
        pbar.close()

    captured = checked_domains().count_documents({"status": "gambling", "screenshot_taken": True})
    failed = checked_domains().count_documents({"status": "gambling", "screenshot_taken": False})
    print(f"[capture] Done. captured={captured} failed={failed}")
