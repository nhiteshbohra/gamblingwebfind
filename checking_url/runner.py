"""
checking_url/runner.py — Pull active domains from domain_Listed, fetch + classify,
write results to checked_domains.
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import find_active_domains, write_result, checked_domains, get_db
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify


async def run(concurrency: int = None, limit: int = 0):
    get_db()
    concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    # Load 500 keywords from JSON file once
    keywords = load_keywords()

    pending = list(find_active_domains(limit=limit))
    if not pending:
        print("[check] No active domains to process (or all already checked).")
        return

    print(f"[check] {len(pending)} active domains to process.")
    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=len(pending), desc="Checking", unit="domain", dynamic_ncols=True)

    async def process(doc):
        domain = doc.get("domain", "")
        if not domain:
            pbar.update(1)
            return

        url = f"https://{domain}"
        async with sem:
            result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

        if result.failure_type:
            status_map = {"blocked": "blocked", "dead_confirmed": "dead", "connection_failed": "dead"}
            write_result(domain, url=url, status=status_map.get(result.failure_type, "dead"), reason=[result.failure_type])
        else:
            # Classify: >= 3 matching keywords = gambling, else regular
            is_gambling, matched_reasons = classify(
                result.html or "", url=url, keywords=keywords, min_keywords=3
            )
            status = "gambling" if is_gambling else "regular"
            write_result(domain, url=url, status=status, reason=matched_reasons)

        pbar.update(1)

    await asyncio.gather(*[process(doc) for doc in pending])
    pbar.close()

    gambling = checked_domains().count_documents({"status": "gambling"})
    blocked  = checked_domains().count_documents({"status": "blocked"})
    dead     = checked_domains().count_documents({"status": "dead"})
    regular  = checked_domains().count_documents({"status": "regular"})
    print(f"\n[check] Done -> gambling={gambling}  blocked={blocked}  dead={dead}  regular={regular}")
