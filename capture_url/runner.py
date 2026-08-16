"""
capture_url/runner.py — Decoupled Report Exporter & Fallback Screenshotter.

Since screenshots are captured immediately during checking_url, this runner:
1. Queries MongoDB for unexported gambling domains (screenshot_taken=True, exported!=True).
2. If any pending gambling domain is missing a screenshot (fallback), it captures it.
3. Returns the list of processed domain IDs for instant Word, PDF & Excel export.
"""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import (
    find_unexported_gambling_domains,
    find_pending_capture,
    checked_domains,
    write_result,
)
from capture_url.screenshot import BrowserPool, is_valid_screenshot
from checking_url.classifier import load_keywords

OUTPUT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def run(concurrency: int = None, limit: int = 0) -> list[str]:
    concurrency = concurrency or int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. First, check if there are any un-screenshotted gambling domains that need fallback capture
    pending_capture = list(find_pending_capture(limit=limit))
    if pending_capture:
        print(f"[export] Found {len(pending_capture)} gambling domain(s) needing screenshot capture...")
        keywords = load_keywords()
        pool = BrowserPool(concurrency=concurrency)
        await pool.start()
        sem = asyncio.Semaphore(concurrency)
        pbar = tqdm(total=len(pending_capture), desc="Capturing Missing", unit="domain", dynamic_ncols=True)

        async def capture_missing(doc):
            domain = doc["_id"]
            url = doc.get("url", f"https://{domain}")
            async with sem:
                path, status, reason = await pool.capture_url(url, OUTPUT_DIR, retries=2, keywords=keywords)
            if path and is_valid_screenshot(path):
                checked_domains().update_one(
                    {"_id": domain},
                    {"$set": {"screenshot_taken": True, "screenshot_failed_reason": None}}
                )
            else:
                fail_msg = str(reason) if reason else "Screenshot failed or timed out"
                checked_domains().update_one(
                    {"_id": domain},
                    {"$set": {"screenshot_taken": False, "screenshot_failed_reason": fail_msg}}
                )
            pbar.update(1)

        try:
            await asyncio.gather(*[capture_missing(doc) for doc in pending_capture])
        finally:
            await pool.close()
            pbar.close()

    # 2. Pull all gambling domains with captured screenshots pending export
    unexported = list(find_unexported_gambling_domains(limit=limit))
    if not unexported:
        # Fallback: check all gambling domains if unexported is empty
        unexported = list(checked_domains().find({"status": "gambling", "screenshot_taken": True}))
        if limit:
            unexported = unexported[:limit]

    processed_ids = [doc["_id"] for doc in unexported]
    print(f"[export] {len(processed_ids)} captured gambling domains ready for export.")
    return processed_ids
