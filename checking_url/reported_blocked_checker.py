"""
checking_url/reported_blocked_checker.py
─────────────────────────────────────────
On-demand recheck for domains you've already exported (reported for blocking). Flips a
domain to "reported_down" once it's confirmed unreachable with real confidence
(see below) -- the expected outcome once an ISP/regulator acts on the report. Found
reachable again on a later run, it reverts straight back to "gambling".

Deliberately NOT a background daemon. The user runs this whenever they want to check on
a reported batch (main.py menu option 5, or `python -m checking_url.reported_blocked_checker`).

Screenshots are never touched here — a reported-down domain is still fundamentally a
confirmed gambling site (its screenshot is exactly the evidence that got it reported in
the first place), so unlike a real reclassification-away-from-gambling, nothing is
deleted. See export_domains/screenshot.py's delete_screenshot() docstring.

CONFIDENCE, not literal 100% certainty -- that's not achievable from outside the network
doing the blocking (an ISP block, the owner taking the site down, and a long outage all
look identical from here). Guarding against a single transient blip: CONFIRM_ATTEMPTS
independent fetches, spaced CONFIRM_DELAY_SECONDS apart, must ALL fail before a run counts
the domain as unreachable at all.
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

from db.mongo_client import find_exported_gambling_domains, write_result, get_db
from checking_url.fetcher import fetch

# failure_types that count as "no longer reachable the normal way" -- the practical
# stand-in for "blocked" (see module docstring). 'parked' (domain resold to a parking
# service) and 'server_error' (transient) are deliberately excluded -- neither is
# evidence of a block, so those are left as still "gambling" for a later recheck.
_BLOCKED_LIKE_FAILURES = {"connection_failed", "dead_confirmed", "blocked"}

CONFIRM_ATTEMPTS = int(os.getenv("REPORTED_BLOCK_CONFIRM_ATTEMPTS", 3))
CONFIRM_DELAY_SECONDS = float(os.getenv("REPORTED_BLOCK_CONFIRM_DELAY", 30))


async def _confirm_unreachable_this_run(url: str, domain: str, fetch_sem: asyncio.Semaphore, timeout: float, delay: float) -> str | None:
    """Run CONFIRM_ATTEMPTS independent, time-spaced fetches. Returns the last failure_type
    only if EVERY attempt failed the same blocked-like way; returns None the moment any
    attempt succeeds (site is live -- no point burning the remaining attempts)."""
    last_failure_type = None
    for attempt in range(CONFIRM_ATTEMPTS):
        async with fetch_sem:
            result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)
        if result.failure_type not in _BLOCKED_LIKE_FAILURES:
            return None
        last_failure_type = result.failure_type
        if attempt < CONFIRM_ATTEMPTS - 1:
            await asyncio.sleep(CONFIRM_DELAY_SECONDS)
    return last_failure_type


async def run(concurrency: int = None, limit: int = 0):
    get_db()
    concurrency = concurrency or int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20)))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    pending = list(find_exported_gambling_domains(limit=limit))
    print(f"[reported_blocked_checker] {len(pending)} exported gambling domains to re-check.")
    print(f"[reported_blocked_checker] Confirmation: {CONFIRM_ATTEMPTS} attempts/run, "
          f"{CONFIRM_DELAY_SECONDS:.0f}s apart, before counting a domain as down.")
    if not pending:
        return {"checked": 0, "now_down": 0, "still_live": 0}

    fetch_sem = asyncio.Semaphore(concurrency)
    stats = {"checked": 0, "now_down": 0, "still_live": 0}
    today = datetime.now(IST).strftime("%Y-%m-%d")

    async def process(doc):
        domain = doc["domain"]
        url = doc["url"]
        was_reported_down = doc.get("status") == "reported_down"
        failure_type = await _confirm_unreachable_this_run(url, domain, fetch_sem, timeout, delay)
        stats["checked"] += 1

        if failure_type is None:
            # Live again -- revert a reported-down domain back to plain "gambling".
            # screenshot_taken=True is NOT optional here: write_result() defaults it to
            # False for any "gambling" write that doesn't pass it explicitly, which would
            # otherwise silently wipe out real, already-captured screenshot evidence on
            # every domain that recovers (confirmed live against MongoDB, 2026-08-30).
            if was_reported_down:
                await asyncio.to_thread(
                    write_result, domain, status="gambling", screenshot_taken=True,
                    reason="Reachable again -- reported-down status cleared", url=url, ip=doc.get("ip"),
                )
            stats["still_live"] += 1
            return

        if not was_reported_down:
            await asyncio.to_thread(
                write_result, domain, status="reported_down",
                reason=f"Confirmed unreachable ({failure_type}), checked {today}",
                url=url, ip=doc.get("ip"),
            )
        stats["now_down"] += 1

    tasks = [process(doc) for doc in pending]
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Rechecking Reported", unit="domain"):
        await coro

    print("\n" + "=" * 65)
    print("            REPORTED-BLOCKED RECHECK SUMMARY")
    print("=" * 65)
    print(f"  Total Re-Checked   : {stats['checked']:,}")
    print(f"  Now Down (reported): {stats['now_down']:,}")
    print(f"  Still Live         : {stats['still_live']:,}")
    print("=" * 65 + "\n")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Recheck exported gambling domains for ISP/regulator blocking")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(run(concurrency=args.concurrency, limit=args.limit))
