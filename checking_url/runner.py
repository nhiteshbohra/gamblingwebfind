"""
checking_url/runner.py — Triple-Lock Async Classifier + Immediate Screenshot Capture.

New Architecture:
1. Fast Fetch via Scrapling AsyncFetcher (curl_cffi TLS impersonation).
2. Layer 2: Heuristic Pre-Screen (988 Keywords, weighted float scoring):
   - Score >= 5.0  -> Confirmed Gambling (no AI needed)
   - Score 2.5-5.0 -> needs_ai (escalated to Ollama)
   - Score < 2.5   -> Regular (no AI needed)
   - Hospitality/educational/e-commerce gatekeeping prevents false locks
3. Layer 3: Ollama AI 2-Round Challenge System:
   - Round 1: Standard classify_with_challenge()
   - Round 2: If Round 1 says 'regular' -> Challenge: AI must prove with specific evidence
   - If evidence vague/low confidence -> override to gambling
   - If Ollama offline + keywords matched -> unconfirmed (re-queued for later)
4. Immediate Screenshot: If status is 'gambling', Playwright BrowserPool captures immediately.
5. MongoDB sync: updates checked_domains and domain_Listed.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

# Silence verbose loggers to keep progress bar clean
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright", "ai_classifier"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import (
    find_active_domains,
    find_blocked_domains,
    find_unconfirmed_domains,
    find_regular_domains,
    find_dead_domains,
    write_result,
    get_db,
)
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify
from checking_url.ai_classifier import classify_with_challenge, close_ai_session, _timeout_mgr
from export_domains.screenshot import BrowserPool, is_valid_screenshot, delete_screenshot

OUTPUT_SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def async_write_result(*args, **kwargs):
    return await asyncio.to_thread(write_result, *args, **kwargs)


async def run(concurrency: int = None, limit: int = 0, mode: str = "new"):
    get_db()
    output_screenshot_dir = os.getenv("SCREENSHOT_DIR", OUTPUT_SCREENSHOT_DIR)
    os.makedirs(output_screenshot_dir, exist_ok=True)
    concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    keywords = load_keywords()

    # Reset the dynamic timeout EMA so a prior crashed/saturated run's inflated
    # timeouts don't carry over and slow down this fresh batch.
    await _timeout_mgr.reset_ema()

    if mode == "blocked":
        pending = list(find_blocked_domains(limit=limit))
        desc_label = "Rechecking Blocked"
    elif mode == "unconfirmed":
        pending = list(find_unconfirmed_domains(limit=limit))
        desc_label = "Rechecking Unconfirmed"
    elif mode == "regular":
        pending = list(find_regular_domains(limit=limit))
        desc_label = "Rechecking Regular"
    elif mode == "dead":
        pending = list(find_dead_domains(limit=limit))
        desc_label = "Rechecking Dead"
    else:
        pending = list(find_active_domains(limit=limit))
        desc_label = "Checking & Capturing"

    if not pending:
        target_name = mode if mode in ("blocked", "unconfirmed", "regular", "dead") else "active unprocessed"
        print(f"[check] No {target_name} domains to process.")
        return {}

    total_pending = len(pending)
    print(f"[check] {total_pending} {mode} domains to process.")

    # Initialize BrowserPool for immediate screenshotting of confirmed gambling sites
    browser_pool = BrowserPool(concurrency=min(concurrency, 10))
    await browser_pool.start()

    fetch_sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_pending, desc=desc_label, unit="domain", dynamic_ncols=True)
    pbar.set_postfix({"Left": total_pending})

    run_stats = {
        "gambling": 0,
        "ai_evaluated": 0,
        "ai_gambling": 0,
        "regular": 0,
        "unconfirmed": 0,
        "blocked": 0,
        "dead": 0,
        "screenshots_taken": 0,
    }

    async def process(doc):
        try:
            domain = doc.get("domain", "")
            if not domain:
                pbar.update(1)
                pbar.set_postfix({"Left": total_pending - pbar.n})
                return

            url = f"https://{domain}"

            # 1. Fetch HTML
            async with fetch_sem:
                result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

            if result.failure_type:
                if result.failure_type == "connection_failed":
                    final_status = "unconfirmed"
                    final_reason = f"Network error: {result.error or 'connection_failed'}"
                elif result.failure_type == "blocked":
                    final_status = "blocked"
                    final_reason = "Blocked: Cloudflare WAF / HTTP 403 Forbidden"
                else:
                    final_status = "dead"
                    final_reason = f"Dead: {result.error or 'unreachable'}"

                delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(domain, url=url, status=final_status, reason=final_reason, screenshot_taken=False)
                run_stats[final_status] += 1
                pbar.update(1)
                pbar.set_postfix({"Left": total_pending - pbar.n})
                return

            # 2. Universal Keyword Threshold:
            #    < 3 keywords   -> Strictly Regular Website
            #    3 to 4 keywords -> Route to AI Classifier
            #    >= 5 keywords  -> Automatically Gambling
            decision, matched_keywords = classify(result.html or "", url=url, keywords=keywords)
            num_matched = len(matched_keywords)

            if decision == "gambling":
                # >= 5 keywords: Keyword-Triggered Gambling
                final_status = "gambling"
                final_reason = f"{num_matched} keywords matched"
                run_stats["gambling"] += 1

            elif decision == "needs_ai":
                # 3 to 4 keywords: Route to AI Classifier
                run_stats["ai_evaluated"] += 1
                ai_res = await classify_with_challenge(
                    result.html or "",
                    url=url,
                    matched_keywords=matched_keywords,
                    fast_mode=(mode == "unconfirmed"),
                )
                ai_verdict = ai_res.get("verdict", "unconfirmed")

                if ai_verdict == "gambling":
                    final_status = "gambling"
                    run_stats["gambling"] += 1
                    run_stats["ai_gambling"] += 1
                    final_reason = ai_res.get("reason", "AI confirmed gambling")
                elif ai_verdict == "regular":
                    final_status = "regular"
                    run_stats["regular"] += 1
                    final_reason = ai_res.get("reason", "AI rejected: Regular website")
                    delete_screenshot(domain, output_screenshot_dir)
                else:
                    # Ollama offline or timeout -> Unconfirmed fallback
                    final_status = "unconfirmed"
                    run_stats["unconfirmed"] += 1
                    final_reason = ai_res.get("reason", "Unconfirmed: AI timeout/offline")
                    delete_screenshot(domain, output_screenshot_dir)

            else:
                # Less than 3 keywords: Strictly Regular Website
                final_status = "regular"
                final_reason = f"{num_matched} keywords matched (regular)" if num_matched > 0 else "0 keywords matched"
                run_stats["regular"] += 1
                delete_screenshot(domain, output_screenshot_dir)

            # 3. Screenshot Capture & Error Management Rules
            # Strict Proof Requirement: Only capture if validated as gambling
            if final_status == "gambling":
                ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                    url, output_screenshot_dir, retries=2, keywords=keywords
                )
                if ss_path and is_valid_screenshot(ss_path):
                    run_stats["screenshots_taken"] += 1
                    await async_write_result(
                        domain,
                        url=url,
                        status="gambling",
                        reason=final_reason,
                        screenshot_taken=True,
                        screenshot_failed_reason=None,
                    )
                else:
                    # Network Error / Unconfirmed Fallback & Immediate Cleanup Rule
                    delete_screenshot(domain, output_screenshot_dir)
                    _DEAD_SS_STATUSES = {"dead", "dns_failed", "connection_refused", "timeout", "ssl_or_reset"}
                    if ss_status == "blocked":
                        await async_write_result(
                            domain,
                            url=url,
                            status="blocked",
                            reason=f"Blocked: {ss_reason}",
                            screenshot_taken=False,
                            screenshot_failed_reason=str(ss_reason),
                        )
                        run_stats["blocked"] += 1
                    elif ss_status in _DEAD_SS_STATUSES:
                        await async_write_result(
                            domain,
                            url=url,
                            status="dead",
                            reason=f"Dead: {ss_reason}",
                            screenshot_taken=False,
                            screenshot_failed_reason=str(ss_reason),
                        )
                        run_stats["dead"] += 1
                    else:
                        await async_write_result(
                            domain,
                            url=url,
                            status="unconfirmed",
                            reason=f"Unconfirmed: Screenshot error ({ss_reason})",
                            screenshot_taken=False,
                            screenshot_failed_reason=str(ss_reason),
                        )
                        run_stats["unconfirmed"] += 1
            else:
                delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(domain, url=url, status=final_status, reason=final_reason, screenshot_taken=False)

        except Exception as e:
            domain = doc.get("domain", "")
            if domain:
                try:
                    await async_write_result(
                        domain,
                        url=f"https://{domain}",
                        status="unconfirmed",
                        reason=f"Pipeline error: {type(e).__name__}: {e}",
                        screenshot_taken=False,
                    )
                    run_stats["unconfirmed"] += 1
                except Exception:
                    pass
            pbar.update(1)
            pbar.set_postfix({"Left": total_pending - pbar.n})

    queue = asyncio.Queue()
    for doc in pending:
        queue.put_nowait(doc)

    async def worker():
        while not queue.empty():
            try:
                doc = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process(doc)
            finally:
                queue.task_done()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
    finally:
        await browser_pool.close()
        await close_ai_session()
        pbar.close()

    print("\n" + "=" * 65)
    summary_title = "BLOCKED RE-CHECK SUMMARY" if mode == "blocked" else "TRIPLE-LOCK AI CAPTURE SUMMARY"
    print(f"                 {summary_title}                 ")
    print("=" * 65)
    print(f" Total Domains Processed           : {total_pending:,}")
    print(f"  * Confirmed Gambling Sites       : {run_stats['gambling']:,}")
    print(f"    - Captured Screenshots         : {run_stats['screenshots_taken']:,}")
    print(f"    - Evaluated by Ollama AI       : {run_stats['ai_evaluated']:,}")
    print(f"    - AI Confirmed Gambling        : {run_stats['ai_gambling']:,}")
    print(f"    - Challenge Round Overrides    : {run_stats.get('challenge_overrides', 0):,}")
    print(f"  * Regular Sites (Verified)       : {run_stats['regular']:,}")
    if run_stats["unconfirmed"]:
        print(f"  * Unconfirmed (Ollama Down)      : {run_stats['unconfirmed']:,} [Queued to re-run]")
    blocked_label = "Still Blocked (403 / WAF)" if mode == "blocked" else "Blocked (403 / WAF)"
    print(f"  * {blocked_label:<31}: {run_stats['blocked']:,}")
    print(f"  * Dead / Unreachable             : {run_stats['dead']:,}")
    print("=" * 65 + "\n")

    return run_stats
