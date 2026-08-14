"""
checking_url/runner.py — Pull active domains from domain_Listed, fetch + classify,
write results to checked_domains.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
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

from db.mongo_client import find_active_domains, find_blocked_domains, write_result, checked_domains, get_db
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify


async def run(concurrency: int = None, limit: int = 0, mode: str = "new"):
    get_db()
    concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    # Load 500 keywords from JSON file once
    keywords = load_keywords()

    if mode == "blocked":
        pending = list(find_blocked_domains(limit=limit))
        desc_label = "Rechecking Blocked"
    else:
        pending = list(find_active_domains(limit=limit))
        desc_label = "Checking New"

    if not pending:
        target_name = "blocked" if mode == "blocked" else "active unprocessed"
        print(f"[check] No {target_name} domains to process.")
        return

    total_pending = len(pending)
    print(f"[check] {total_pending} {mode} domains to process.")
    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_pending, desc=desc_label, unit="domain", dynamic_ncols=True)
    pbar.set_postfix({"Left": total_pending})

    run_stats = {
        "gambling": 0,
        "regular": 0,
        "blocked": 0,
        "dead": 0,
    }

    async def process(doc):
        domain = doc.get("domain", "")
        if not domain:
            pbar.update(1)
            pbar.set_postfix({"Left": total_pending - pbar.n})
            return

        url = f"https://{domain}"
        async with sem:
            result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

        if result.failure_type:
            status_map = {"blocked": "blocked", "dead_confirmed": "dead", "connection_failed": "dead"}
            final_status = status_map.get(result.failure_type, "dead")
            write_result(domain, url=url, status=final_status, reason=[result.failure_type])
            run_stats[final_status] += 1
        else:
            # Classify: >= 3 matching keywords = gambling, else regular
            is_gambling, matched_reasons = classify(
                result.html or "", url=url, keywords=keywords, min_keywords=3
            )
            status = "gambling" if is_gambling else "regular"
            write_result(domain, url=url, status=status, reason=matched_reasons)
            run_stats[status] += 1

        pbar.update(1)
        pbar.set_postfix({"Left": total_pending - pbar.n})

    await asyncio.gather(*[process(doc) for doc in pending])
    pbar.close()

    print("\n" + "=" * 60)
    summary_title = "BLOCKED RE-CHECK SUMMARY" if mode == "blocked" else "CHECKING RESULTS SUMMARY"
    print(f"                {summary_title}                ")
    print("=" * 60)
    print(f" Total URLs Processed in this run : {total_pending:,}")
    print(f"  * Gambling Sites (Confirmed)   : {run_stats['gambling']:,}")
    print(f"  * Regular / Rejected Sites     : {run_stats['regular']:,}")
    blocked_label = "Still Blocked (403 / WAF)" if mode == "blocked" else "Blocked (403 / WAF)"
    print(f"  * {blocked_label:<30}: {run_stats['blocked']:,}")
    print(f"  * Dead / Unreachable           : {run_stats['dead']:,}")
    print("=" * 60)

    return run_stats
