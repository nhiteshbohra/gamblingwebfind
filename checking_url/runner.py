"""
checking_url/runner.py — Triple-Lock Async Classifier + Immediate Screenshot Capture.

New Architecture:
1. Fast Fetch via Scrapling AsyncFetcher (curl_cffi TLS impersonation).
2. Layer 2: Heuristic Pre-Screen (Expanded 700+ Keywords):
   - >= 5 keywords -> Confirmed Gambling (no AI needed)
   - 0-4 keywords  -> needs_ai (ALL live sites go through Ollama)
   - Parked sites  -> needs_ai (AI confirms, no longer auto-rejected)
3. Layer 3: Ollama AI 2-Round Challenge System:
   - Round 1: Standard classify_with_challenge()
   - Round 2: If Round 1 says 'regular' -> Challenge: AI must prove with specific evidence
   - If evidence vague/low confidence -> override to gambling
   - If Ollama offline + keywords matched -> gambling (not unconfirmed)
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

from db.mongo_client import find_active_domains, find_blocked_domains, find_unconfirmed_domains, write_result, get_db
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify
from checking_url.ai_classifier import classify_with_challenge, close_ai_session
from capture_url.screenshot import BrowserPool, is_valid_screenshot

OUTPUT_SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def run(concurrency: int = None, limit: int = 0, mode: str = "new"):
    get_db()
    concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    keywords = load_keywords()

    if mode == "blocked":
        pending = list(find_blocked_domains(limit=limit))
        desc_label = "Rechecking Blocked"
    elif mode == "unconfirmed":
        pending = list(find_unconfirmed_domains(limit=limit))
        desc_label = "Rechecking Unconfirmed"
    else:
        pending = list(find_active_domains(limit=limit))
        desc_label = "Checking & Capturing"

    if not pending:
        target_name = mode if mode in ("blocked", "unconfirmed") else "active unprocessed"
        print(f"[check] No {target_name} domains to process.")
        return {}

    total_pending = len(pending)
    print(f"[check] {total_pending} {mode} domains to process.")
    os.makedirs(OUTPUT_SCREENSHOT_DIR, exist_ok=True)

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
            status_map = {"blocked": "blocked", "dead_confirmed": "dead", "connection_failed": "dead"}
            final_status = status_map.get(result.failure_type, "dead")
            write_result(domain, url=url, status=final_status, reason=[result.failure_type])
            run_stats[final_status] += 1
            pbar.update(1)
            pbar.set_postfix({"Left": total_pending - pbar.n})
            return

        # 2. Layer 2: Heuristic Pre-Screen (700+ keywords)
        decision, matched_reasons = classify(result.html or "", url=url, keywords=keywords)

        final_status = "regular"
        final_reasons = matched_reasons

        if decision == "gambling":
            # >= 5 keywords -> Confirmed Gambling immediately
            final_status = "gambling"
            run_stats["gambling"] += 1

        elif decision == "needs_ai":
            # All live sites with < 5 keywords -> 2-Round AI Challenge
            run_stats["ai_evaluated"] += 1
            ai_res = await classify_with_challenge(
                result.html or "",
                url=url,
                matched_keywords=matched_reasons
            )
            ai_verdict = ai_res.get("verdict", "unconfirmed")

            if ai_verdict == "gambling":
                final_status = "gambling"
                run_stats["gambling"] += 1
                run_stats["ai_gambling"] += 1
                final_reasons = list(set(matched_reasons + ai_res.get("key_triggers", [])))
                if not final_reasons and ai_res.get("reason"):
                    final_reasons = [ai_res["reason"]]
                if ai_res.get("challenge_override"):
                    run_stats["challenge_overrides"] = run_stats.get("challenge_overrides", 0) + 1
            elif ai_verdict == "regular":
                final_status = "regular"
                run_stats["regular"] += 1
                final_reasons = [ai_res.get("reason", "AI challenge verified regular")]
            else:
                # Ollama was offline or timed out -> unconfirmed (re-queued)
                final_status = "unconfirmed"
                run_stats["unconfirmed"] += 1
                final_reasons = ["ollama_offline_or_timeout"]

        else:
            # Strong negative archetype (4+ signals, 0 keywords) -> regular
            final_status = "regular"
            run_stats["regular"] += 1

        # 3. If Gambling -> Take Screenshot Immediately
        if final_status == "gambling":
            ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                url, OUTPUT_SCREENSHOT_DIR, retries=2, keywords=keywords
            )
            if ss_path and is_valid_screenshot(ss_path):
                run_stats["screenshots_taken"] += 1
                write_result(
                    domain,
                    url=url,
                    status="gambling",
                    reason=final_reasons,
                    screenshot_taken=True,
                    screenshot_failed_reason=None,
                )
            else:
                fail_msg = str(ss_reason) if ss_reason else "Screenshot failed or timed out"
                write_result(
                    domain,
                    url=url,
                    status="gambling",
                    reason=final_reasons,
                    screenshot_taken=False,
                    screenshot_failed_reason=fail_msg,
                )
        else:
            write_result(domain, url=url, status=final_status, reason=final_reasons)

        except Exception as e:
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
