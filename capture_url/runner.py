"""
capture_url/runner.py — Decoupled Report Exporter.

This runner:
1. Queries MongoDB for unexported gambling domains (status="gambling", screenshot_taken=True, exported=False).
2. Verifies that the screenshot file actually exists on disk in output/screenshots and is valid.
3. If screenshot is missing/invalid on disk, updates MongoDB:
   screenshot_taken=False, screenshot_failed_reason="Screenshot JPEG missing on disk".
4. Returns the list of verified domain IDs for instant Word, PDF & Excel export.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import (
    find_unexported_gambling_domains,
    checked_domains,
)
from capture_url.screenshot import _url_to_filename, is_valid_screenshot

OUTPUT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def run(concurrency: int = None, limit: int = 0) -> list[str]:
    output_screenshot_dir = os.getenv("SCREENSHOT_DIR", OUTPUT_DIR)
    os.makedirs(output_screenshot_dir, exist_ok=True)

    # 1. Pull all gambling domains with screenshot_taken=True pending export
    unexported = list(find_unexported_gambling_domains(limit=limit))
    if not unexported:
        # Fallback query if unexported is empty
        unexported = list(checked_domains().find({
            "status": "gambling",
            "screenshot_taken": True,
            "exported": {"$ne": True}
        }))
        if limit:
            unexported = unexported[:limit]

    verified_ids = []
    missing_ids = []

    for doc in unexported:
        domain = doc.get("domain") or doc.get("_id")
        url = doc.get("url") or f"https://{domain}"
        candidates = [
            _url_to_filename(url),
            _url_to_filename(f"https://{domain}"),
            _url_to_filename(f"http://{domain}"),
        ]
        found = False
        for cand in candidates:
            cand_path = os.path.join(output_screenshot_dir, cand)
            if os.path.exists(cand_path) and is_valid_screenshot(cand_path):
                found = True
                break

        if found:
            verified_ids.append(domain)
        else:
            missing_ids.append(domain)

    if missing_ids:
        print(f"[export] {len(missing_ids)} gambling domain(s) have screenshot_taken=True but file missing on disk.")
        print(f"[export] Auto-capturing missing screenshots before export...")
        # Reset flag so screenshot_runner can pick them up
        checked_domains().update_many(
            {"_id": {"$in": missing_ids}},
            {"$set": {"screenshot_taken": False, "screenshot_failed_reason": "Screenshot JPEG missing on disk"}},
        )
        try:
            from capture_url.screenshot_runner import run as screenshot_run
            cap_result = await screenshot_run(domain_ids=missing_ids)
            # Re-check which ones now have valid screenshots on disk
            for domain in missing_ids:
                url = f"https://{domain}"
                candidates = [
                    _url_to_filename(url),
                    _url_to_filename(f"https://{domain}"),
                    _url_to_filename(f"http://{domain}"),
                ]
                for cand in candidates:
                    if os.path.exists(os.path.join(output_screenshot_dir, cand)) and is_valid_screenshot(os.path.join(output_screenshot_dir, cand)):
                        verified_ids.append(domain)
                        break
        except Exception as e:
            print(f"[export] Screenshot auto-capture warning: {e}")

    print(f"[export] {len(verified_ids)} verified gambling domains with present screenshots ready for export.")
    return verified_ids

