"""
checking_url/known_gambling_runner.py
─────────────────────────────────────
Import pre-confirmed gambling domains from a flat text file (one domain / URL
per line), insert them into MongoDB, then try to visit each one:

  • Active / reachable  → take a full-page screenshot, mark status='gambling',
                          screenshot_taken=True
  • Any error / dead   → mark status='dead', screenshot_taken=False

Resume support: a JSON checkpoint file tracks which domains have already been
processed so that if the system shuts down mid-run, the next run picks up from
exactly where it left off — no domain is processed twice.

Checkpoint file location (default): output/known_gambling_progress.json
"""

import asyncio
import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── ensure project root on sys.path ──────────────────────────────────────────
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(_ROOT) / ".env")

# Silence noisy third-party loggers so tqdm stays readable
for _n in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_n)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

from tqdm import tqdm

from db.mongo_client import (
    checked_domains,
    source_domains,
    extract_domains_from_file,
    resolve_ip,
    IST,
)
from checking_url.fetcher import fetch
from export_domains.screenshot import BrowserPool, is_valid_screenshot, delete_screenshot

# ── constants ─────────────────────────────────────────────────────────────────
OUTPUT_SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))
DEFAULT_CHECKPOINT = os.path.join("output", "known_gambling_progress.json")
DEFAULT_CONCURRENCY = int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 10)))
DEFAULT_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", 15))
DEFAULT_DELAY = float(os.getenv("PER_DOMAIN_DELAY", 1.0))


# ── checkpoint helpers ────────────────────────────────────────────────────────

def _load_checkpoint(checkpoint_path: str) -> dict:
    """Load progress checkpoint. Returns {done: set, stats: dict}."""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                "done": set(raw.get("done", [])),
                "stats": raw.get("stats", {"gambling_active": 0, "dead": 0, "regular": 0, "screenshots": 0}),
            }
        except Exception:
            pass
    return {"done": set(), "stats": {"gambling_active": 0, "dead": 0, "regular": 0, "screenshots": 0}}


def _save_checkpoint(checkpoint_path: str, done: set, stats: dict):
    """Atomically save progress to checkpoint file."""
    os.makedirs(os.path.dirname(os.path.abspath(checkpoint_path)), exist_ok=True)
    tmp = checkpoint_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": sorted(done), "stats": stats}, f, indent=2)
        os.replace(tmp, checkpoint_path)
    except Exception as e:
        print(f"[!] Checkpoint save error: {e}")


# ── MongoDB upsert helpers ────────────────────────────────────────────────────

def _upsert_domain_listed(domain: str, import_tag: str):
    """Ensure domain exists in domain_Listed as active+processed (known gambling).
    Always stamps source so existing docs are updated too.
    """
    today = datetime.now(IST).strftime("%Y-%m-%d")
    source_domains().update_one(
        {"_id": domain},
        {
            "$set": {
                "domain": domain,
                "active": True,
                "processed": True,
                "source": import_tag,
            },
            "$setOnInsert": {"added_date": today},
        },
        upsert=True,
    )


def _write_gambling_active(
    domain: str,
    url: str,
    screenshot_taken: bool,
    import_tag: str,
    screenshot_failed_reason: str | None = None,
    ip: str = None,
):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if not ip:
        ip = resolve_ip(domain)

    set_fields = {
        "domain": domain,
        "url": url,
        "status": "gambling",
        "reason": "Known gambling — confirmed true positive",
        "screenshot_taken": screenshot_taken,
        "screenshot_failed_reason": screenshot_failed_reason,
        "source": import_tag,
    }
    if ip:
        set_fields["ip"] = ip
    if screenshot_taken:
        set_fields["screenshot_date"] = today

    checked_domains().update_one(
        {"_id": domain},
        {"$set": set_fields, "$setOnInsert": {"added_date": today}},
        upsert=True,
    )
def _write_dead(domain: str, url: str, reason: str, import_tag: str, ip: str = None):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if not ip:
        ip = resolve_ip(domain)

    set_fields = {
        "domain": domain,
        "url": url,
        "status": "dead",
        "reason": f"Dead: {reason}",
        "source": import_tag,
    }
    if ip:
        set_fields["ip"] = ip

    checked_domains().update_one(
        {"_id": domain},
        {
            "$set": set_fields,
            "$setOnInsert": {"added_date": today},
        },
        upsert=True,
    )
    # Mark dead in source collection + stamp source
    source_domains().update_one(
        {"_id": domain},
        {
            "$set": {
                "domain": domain,
                "active": False,
                "processed": True,
                "source": import_tag,
            },
            "$setOnInsert": {"added_date": today},
        },
        upsert=True,
    )


def _write_regular(domain: str, url: str, reason: str, import_tag: str, ip: str = None):
    """Domain is reachable but not gambling — used for parked/for-sale registrar landers,
    which are live content, just not gambling and not dead."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if not ip:
        ip = resolve_ip(domain)

    set_fields = {
        "domain": domain,
        "url": url,
        "status": "regular",
        "reason": f"Regular: {reason}",
        "source": import_tag,
    }
    if ip:
        set_fields["ip"] = ip

    checked_domains().update_one(
        {"_id": domain},
        {
            "$set": set_fields,
            "$setOnInsert": {"added_date": today},
        },
        upsert=True,
    )
    # Not gambling — keep it out of the active gambling-recheck queue, but this is a live
    # domain (unlike _write_dead), just stamp source and processed state.
    source_domains().update_one(
        {"_id": domain},
        {
            "$set": {
                "domain": domain,
                "active": False,
                "processed": True,
                "source": import_tag,
            },
            "$setOnInsert": {"added_date": today},
        },
        upsert=True,
    )


def _write_keep_status(domain: str, url: str, existing_status: str, import_tag: str):
    """Re-confirm dead/blocked status in both collections when site is still unreachable.
    Still stamps source so we know this domain was seen in the import file.
    """
    checked_domains().update_one(
        {"_id": domain},
        {"$set": {"source": import_tag}},
    )
    # domain_Listed: keep active=False + stamp source
    base_set = {
        "active": False,
        "processed": True,
        "source": import_tag,
    }
    if existing_status == "blocked":
        base_set["block_reason"] = "blocked"
        source_domains().update_one({"_id": domain}, {"$set": base_set})
    else:  # dead
        source_domains().update_one(
            {"_id": domain},
            {"$set": base_set, "$unset": {"block_reason": ""}},
        )


# ── smart pre-check helper ────────────────────────────────────────────────────

def _existing_db_state(domain: str) -> dict | None:
    """
    Return the checked_domains document for domain (projection only),
    or None if the domain has never been processed.
    Fields returned: status, screenshot_taken, exported
    """
    return checked_domains().find_one(
        {"_id": domain},
        {"status": 1, "screenshot_taken": 1, "exported": 1},
    )


def _source_active(domain: str) -> bool | None:
    """Return domain_Listed.active value, or None if not in collection."""
    doc = source_domains().find_one({"_id": domain}, {"active": 1})
    if doc is None:
        return None
    return bool(doc.get("active", True))


# ── ProcessMode enum (internal) ───────────────────────────────────────────────
# Controls what the process() coroutine should do for a domain.

_MODE_FULL       = "full"          # New domain: fetch + screenshot + write gambling/dead
_MODE_SS_ONLY    = "ss_only"       # Already gambling but screenshot/export incomplete: screenshot only
_MODE_RECHECK    = "recheck"       # Exists as non-gambling: fetch liveness, promote if alive else keep
_MODE_SKIP       = "skip"          # Fully done (gambling + screenshot + exported): skip


def _decide_mode(domain: str, screenshot_dir: str | None = None) -> str:
    """
    Examine MongoDB state and disk screenshot presence:

    • status='gambling' & screenshot file missing on disk      → SS_ONLY (capture screenshot)
    • status='gambling' & screenshot file exists & exported   → SKIP
    • status='gambling' & screenshot or export incomplete     → SS_ONLY
    • status in (regular, unconfirmed, blocked, dead)         → RECHECK (liveness + promote if alive)
    • Not in DB at all                                        → FULL
    """
    doc = _existing_db_state(domain)
    if doc is None:
        return _MODE_FULL

    status = doc.get("status", "")

    if status == "gambling":
        from export_domains.screenshot import find_screenshot_path
        has_file = bool(find_screenshot_path(f"https://{domain}", domain, screenshot_dir))
        if not has_file:
            return _MODE_SS_ONLY       # Physical image file missing on disk -> must capture it

        ss_done = bool(doc.get("screenshot_taken"))
        exported = bool(doc.get("exported"))
        if ss_done and exported and has_file:
            return _MODE_SKIP          # Fully processed — nothing to do
        return _MODE_SS_ONLY           # Needs screenshot or re-export

    # Any other status (regular / unconfirmed / blocked / dead) → recheck liveness
    return _MODE_RECHECK



async def run(
    domains_file: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    checkpoint_path: str = DEFAULT_CHECKPOINT,
):
    """
    Main entry point.

    Parameters
    ----------
    domains_file   : path to the flat text file (one domain/URL per line)
    concurrency    : how many domains to process in parallel
    checkpoint_path: where to persist progress so runs can be resumed
    """
    screenshot_dir = os.getenv("SCREENSHOT_DIR", OUTPUT_SCREENSHOT_DIR)
    os.makedirs(screenshot_dir, exist_ok=True)
    timeout = DEFAULT_TIMEOUT
    delay = DEFAULT_DELAY

    # 1. Load & deduplicate domains from file
    print(f"[+] Loading domains from: {domains_file}")
    all_domains = extract_domains_from_file(domains_file)
    if not all_domains:
        print("[!] No valid domains found in file.")
        return

    total_in_file = len(all_domains)
    print(f"[+] {total_in_file:,} unique domains loaded from file.")

    # Build import source tag — stored as 'source' in MongoDB on every domain touched
    file_stem = Path(domains_file).name          # e.g. gambling_merged_unique_domains.txt
    import_date = datetime.now(IST).strftime("%Y-%m-%d")
    import_tag = f"imported from {file_stem} on {import_date}"
    print(f"[+] Import tag   : {import_tag}")

    # 2. Resume from checkpoint
    cp = _load_checkpoint(checkpoint_path)
    done_set: set = cp["done"]
    stats: dict = cp["stats"]
    # Ensure all stat keys exist (backward-compat with older checkpoints)
    for _k in ("gambling_active", "dead", "regular", "screenshots", "skipped", "promoted", "kept_status"):
        stats.setdefault(_k, 0)

    pending = [d for d in all_domains if d not in done_set]
    already_done = total_in_file - len(pending)
    if already_done > 0:
        print(f"[+] Resuming: {already_done:,} already done, {len(pending):,} remaining.")
    else:
        print(f"[+] Fresh run: {len(pending):,} domains to process.")

    if not pending:
        print("[+] All domains already processed. Nothing to do.")
        _print_summary(stats, total_in_file)
        return

    # 3. Start browser pool
    browser_pool = BrowserPool(concurrency=min(concurrency, 8))
    try:
        await browser_pool.start()
    except Exception as e:
        print(f"[!] Could not start browser pool: {e}")
        print("    Run: playwright install chromium")
        return

    fetch_sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=len(pending), desc="Known Gambling Scan", unit="domain", dynamic_ncols=True)

    # checkpoint flush counter — save every N completions
    _CHECKPOINT_EVERY = 25
    _since_last_save = 0
    _lock = asyncio.Lock()

    async def process(domain: str):
        nonlocal _since_last_save
        url = f"https://{domain}"

        try:
            # ── Step 0: Smart pre-check against MongoDB ───────────────────
            mode = await asyncio.to_thread(_decide_mode, domain, screenshot_dir)

            if mode == _MODE_SKIP:
                # Already fully processed: gambling + screenshot + exported
                async with _lock:
                    stats["skipped"] += 1
                return

            if mode == _MODE_SS_ONLY:
                # Already confirmed gambling — only need to capture screenshot
                ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                    url, screenshot_dir, retries=2, keywords=None, domain=domain
                )
                if ss_path and is_valid_screenshot(ss_path):
                    await asyncio.to_thread(_write_gambling_active, domain, url, True, import_tag)
                    async with _lock:
                        stats["gambling_active"] += 1
                        stats["screenshots"] += 1
                else:
                    delete_screenshot(domain, screenshot_dir)
                    _DEAD_SS = {"dead", "dns_failed", "connection_refused", "timeout", "ssl_or_reset"}
                    if ss_status in _DEAD_SS:
                        # Site went down since last check
                        await asyncio.to_thread(_write_dead, domain, url, ss_reason or "unreachable", import_tag)
                        async with _lock:
                            stats["dead"] += 1
                    else:
                        await asyncio.to_thread(_write_gambling_active, domain, url, False, import_tag, ss_reason)
                        async with _lock:
                            stats["gambling_active"] += 1
                return

            if mode == _MODE_RECHECK:
                # Domain exists but was regular / unconfirmed / blocked / dead.
                # Run liveness check: if alive → promote to gambling + screenshot
                #                     if still dead/blocked → keep existing status
                existing_doc = await asyncio.to_thread(_existing_db_state, domain)
                existing_status = (existing_doc or {}).get("status", "dead")

                async with fetch_sem:
                    result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

                if result.failure_type == "parked":
                    # Now a reachable parked/for-sale registrar lander — not gambling, not
                    # dead, so reclassify as regular regardless of its previous status.
                    reason = result.error or "Parked/For-Sale domain lander detected"
                    await asyncio.to_thread(_write_regular, domain, url, reason, import_tag)
                    async with _lock:
                        stats["regular"] += 1
                    return

                if result.failure_type:
                    # Still unreachable → keep existing status, just refresh last_checked_at
                    await asyncio.to_thread(_write_keep_status, domain, url, existing_status, import_tag)
                    async with _lock:
                        stats["kept_status"] += 1
                    return

                # Site is alive! Promote to gambling + screenshot
                ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                    url, screenshot_dir, retries=2, keywords=None, domain=domain
                )
                if ss_path and is_valid_screenshot(ss_path):
                    await asyncio.to_thread(_write_gambling_active, domain, url, True, import_tag)
                    async with _lock:
                        stats["gambling_active"] += 1
                        stats["screenshots"] += 1
                        stats["promoted"] += 1
                else:
                    delete_screenshot(domain, screenshot_dir)
                    _DEAD_SS = {"dead", "dns_failed", "connection_refused", "timeout", "ssl_or_reset"}
                    if ss_status in _DEAD_SS:
                        await asyncio.to_thread(_write_keep_status, domain, url, existing_status, import_tag)
                        async with _lock:
                            stats["kept_status"] += 1
                    else:
                        # Alive but screenshot blocked → still gambling
                        await asyncio.to_thread(_write_gambling_active, domain, url, False, import_tag, ss_reason)
                        async with _lock:
                            stats["gambling_active"] += 1
                            stats["promoted"] += 1
                return

            # ── _MODE_FULL: brand-new domain ──────────────────────────────
            # ── Step 1: HTTP fetch (liveness probe) ───────────────────────
            async with fetch_sem:
                result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

            if result.failure_type == "parked":
                # Reachable parked/for-sale registrar lander — not gambling, not dead.
                reason = result.error or "Parked/For-Sale domain lander detected"
                await asyncio.to_thread(_write_regular, domain, url, reason, import_tag)
                async with _lock:
                    stats["regular"] += 1
                return

            if result.failure_type:
                # Any other fetch error (connection failed / true 404) → dead
                reason = result.error or result.failure_type or "Unreachable"
                await asyncio.to_thread(_write_dead, domain, url, reason, import_tag)
                async with _lock:
                    stats["dead"] += 1
                return

            # Redundant safety net: check the page body directly for parked / placeholder
            # lander markers in case fetcher-level detection missed it (e.g. marker list
            # drift between the two checks).
            from checking_url.classifier import is_parked_or_for_sale, _extract_text
            html_text = _extract_text(result.html or "")
            is_parked, parked_hits = is_parked_or_for_sale(html_text, result.html, url)
            if is_parked:
                await asyncio.to_thread(_write_regular, domain, url, f"Parked page detected: {', '.join(parked_hits[:2])}", import_tag)
                async with _lock:
                    stats["regular"] += 1
                return

            # Thin placeholder/coming soon stub detection
            words = html_text.split()
            if len(words) <= 12:
                stub_triggers = ("ready", "added", "coming", "soon", "dynadot", "registered", "t0 be added", "content is")
                if any(w in html_text for w in stub_triggers):
                    await asyncio.to_thread(_write_dead, domain, url, "Blank placeholder / coming soon stub lander", import_tag)
                    async with _lock:
                        stats["dead"] += 1
                    return

            # ── Step 2: Screenshot on fully loaded page ───────────────────
            ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                url, screenshot_dir, retries=2, keywords=None, domain=domain
            )

            if ss_path and is_valid_screenshot(ss_path):
                await asyncio.to_thread(_write_gambling_active, domain, url, True, import_tag)
                async with _lock:
                    stats["gambling_active"] += 1
                    stats["screenshots"] += 1
            else:
                delete_screenshot(domain, screenshot_dir)
                _DEAD_SS = {"dead", "dns_failed", "connection_refused", "timeout", "ssl_or_reset"}
                if ss_status in _DEAD_SS:
                    await asyncio.to_thread(_write_dead, domain, url, ss_reason or "screenshot timeout/unreachable", import_tag)
                    async with _lock:
                        stats["dead"] += 1
                else:
                    # Reachable but screenshot blocked — still mark gambling
                    await asyncio.to_thread(_write_gambling_active, domain, url, False, import_tag, ss_reason)
                    async with _lock:
                        stats["gambling_active"] += 1

        except Exception as e:
            await asyncio.to_thread(_write_dead, domain, url, f"{type(e).__name__}: {e}", import_tag)
            async with _lock:
                stats["dead"] += 1

        finally:
            async with _lock:
                done_set.add(domain)
                _since_last_save += 1
                pbar.update(1)
                pbar.set_postfix({
                    "Active": stats["gambling_active"],
                    "Dead": stats["dead"],
                    "Regular": stats["regular"],
                    "Skip": stats["skipped"],
                    "SS": stats["screenshots"],
                })
                if _since_last_save >= _CHECKPOINT_EVERY:
                    _save_checkpoint(checkpoint_path, done_set, stats)
                    _since_last_save = 0

    # 4. Worker queue
    queue: asyncio.Queue = asyncio.Queue()
    for d in pending:
        queue.put_nowait(d)

    async def worker():
        while not queue.empty():
            try:
                domain = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process(domain)
            finally:
                queue.task_done()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        print("\n[+] Interrupted — saving checkpoint...")
    finally:
        # Final checkpoint save
        _save_checkpoint(checkpoint_path, done_set, stats)
        await browser_pool.close()
        pbar.close()

    _print_summary(stats, total_in_file)
    print(f"[+] Progress saved to: {os.path.abspath(checkpoint_path)}")
    print("    Run again to resume from where you left off.\n")
    return stats


def _print_summary(stats: dict, total: int):
    done = stats.get("gambling_active", 0) + stats.get("dead", 0) + stats.get("regular", 0) + stats.get("kept_status", 0)
    print("\n" + "=" * 65)
    print("         KNOWN GAMBLING DOMAINS — SCAN SUMMARY")
    print("=" * 65)
    print(f"  Total domains in file          : {total:,}")
    print(f"  Processed this session         : {done:,}")
    print(f"  Skipped (already complete)     : {stats.get('skipped', 0):,}")
    print(f"  ├─ Active (gambling)           : {stats.get('gambling_active', 0):,}")
    print(f"  │   ├─ Screenshots taken       : {stats.get('screenshots', 0):,}")
    print(f"  │   └─ Promoted from other     : {stats.get('promoted', 0):,}")
    print(f"  ├─ Regular (parked/for-sale)   : {stats.get('regular', 0):,}")
    print(f"  ├─ Dead / Unreachable          : {stats.get('dead', 0):,}")
    print(f"  └─ Kept existing status        : {stats.get('kept_status', 0):,}  (dead/blocked, still offline)")
    print("=" * 65 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Import/check known gambling domains (.xlsx, .csv, .txt) and capture missing screenshots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", nargs="?", default=None, help="Path to Excel/CSV/TXT file of domains")
    parser.add_argument("--file", "-f", dest="file_opt", default=None, help="Alternative flag for domains file")
    parser.add_argument("--concurrency", "-c", type=int, default=DEFAULT_CONCURRENCY, help=f"Browser/fetch concurrency (default: {DEFAULT_CONCURRENCY})")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help=f"Checkpoint file path (default: {DEFAULT_CHECKPOINT})")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Start fresh without loading existing checkpoint")
    args = parser.parse_args()

    target_file = args.file or args.file_opt
    if not target_file:
        target_file = input("Enter path to domains file (.xlsx / .csv / .txt): ").strip().strip('"').strip("'")

    if not target_file or not os.path.exists(target_file):
        print(f"[!] Error: File not found: '{target_file}'")
        sys.exit(1)

    cp_path = args.checkpoint
    if args.reset_checkpoint and os.path.exists(cp_path):
        try:
            os.remove(cp_path)
            print(f"[+] Reset checkpoint file: {cp_path}")
        except Exception as e:
            print(f"[!] Could not remove checkpoint: {e}")

    asyncio.run(run(domains_file=target_file, concurrency=args.concurrency, checkpoint_path=cp_path))


if __name__ == "__main__":
    main()
