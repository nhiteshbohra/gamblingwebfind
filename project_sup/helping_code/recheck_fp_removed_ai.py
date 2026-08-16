"""
project_sup/helping_code/recheck_fp_removed_ai.py
Scans output/screenshots_fp_removed/ and performs a full live HTTP fetch + AI Challenge
classification on each domain.
Any confirmed gambling / betting / casino domains are immediately restored back to
output/screenshots/ and updated in MongoDB as status='gambling'.
"""
import sys
import os
import shutil
import asyncio
from pathlib import Path
from tqdm.asyncio import tqdm_asyncio

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db.mongo_client import checked_domains, write_result, extract_domain
from checking_url.fetcher import fetch
from checking_url.classifier import classify, load_keywords
from checking_url.ai_classifier import (
    classify_with_challenge,
    check_ollama_status,
    close_ai_session,
    is_gambling_domain,
    detect_igaming_providers,
    detect_gambling_funnels,
)

SCREENSHOTS_DIR = ROOT_DIR / "output" / "screenshots"
FP_REMOVED_DIR = ROOT_DIR / "output" / "screenshots_fp_removed"


def filename_to_url_and_domain(fn: str, db_cache: dict) -> tuple[str, str]:
    """Derive domain and target URL accurately from filename or DB cache."""
    # Check if direct DB record exists
    if fn in db_cache:
        doc = db_cache[fn]
        domain = doc.get("domain") or doc.get("_id")
        url = doc.get("url") or f"https://{domain}"
        return url, domain

    # Remove hash and extension: e.g. "adda52_com_855bcf9a.jpg" -> "adda52_com"
    base = fn.rsplit(".", 1)[0]
    parts = base.split("_")

    # If trailing token is a hash (e.g. 8 chars hex), strip it
    if len(parts) >= 2 and len(parts[-1]) in (8, 32) and all(c in "0123456789abcdefABCDEF" for c in parts[-1]):
        parts = parts[:-1]

    raw_domain = ".".join(parts).replace("-", ".")
    # Fix common TLD reconstructions
    # e.g. "aposta-facil.casino"
    raw_domain = raw_domain.replace(".casino", ".casino").replace(".bet", ".bet").replace(".com", ".com").replace(".net", ".net").replace(".org", ".org").replace(".in", ".in")

    domain = extract_domain(f"https://{raw_domain}") or raw_domain
    url = f"https://{raw_domain}"
    return url, domain


async def recheck_fp_removed(concurrency: int = 15):
    if not FP_REMOVED_DIR.exists():
        print(f"[recheck_fp] {FP_REMOVED_DIR} does not exist.")
        return

    # Check Ollama health
    is_up, msg = await check_ollama_status()
    print(f"[recheck_fp] Local AI Status: {msg}")

    files = list(FP_REMOVED_DIR.glob("*.jpg")) + list(FP_REMOVED_DIR.glob("*.png"))
    print(f"[recheck_fp] Found {len(files)} screenshot(s) in {FP_REMOVED_DIR.name} to evaluate.")

    if not files:
        print("[recheck_fp] No files in fp_removed to recheck.")
        return

    # Build filename lookup from DB
    print("[recheck_fp] Building MongoDB record map...", flush=True)
    all_records = checked_domains().find({}, {"_id": 1, "domain": 1, "url": 1, "screenshot_path": 1, "reason": 1})
    db_cache = {}
    for doc in all_records:
        sp = doc.get("screenshot_path")
        if sp:
            db_cache[Path(sp).name] = doc
        d = doc.get("domain") or doc.get("_id")
        if d:
            db_cache[d] = doc

    keywords = load_keywords()

    stats = {
        "restored_gambling": 0,
        "kept_regular": 0,
        "kept_dead": 0,
        "kept_blocked": 0,
        "unconfirmed": 0,
    }
    stats_lock = asyncio.Lock()

    queue = asyncio.Queue()
    for f in files:
        queue.put_nowait(f)

    pbar = tqdm_asyncio(total=len(files), desc="AI Re-checking Removed Images")

    async def worker():
        while not queue.empty():
            try:
                img_path = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            fn = img_path.name
            url, domain = filename_to_url_and_domain(fn, db_cache)

            try:
                # ── 1. Check High-Conviction Domain Anchor First ───────────
                is_g_domain, domain_signal = is_gambling_domain(domain)

                # ── 2. Live HTTP Fetch ─────────────────────────────────────
                fetch_res = await fetch(url, domain, timeout_seconds=12, retries=2)
                html = fetch_res.html or ""

                is_gambling = False
                matched_reasons = []

                if fetch_res.failure_type == 'blocked':
                    final_status = "blocked"
                    matched_reasons = [fetch_res.error or "HTTP 403 / Cloudflare WAF"]
                elif fetch_res.failure_type in ('dead_confirmed', 'connection_failed'):
                    # If dead but domain anchor is explicitly gambling (e.g. *.casino, *.bet)
                    if is_g_domain:
                        is_gambling = True
                        final_status = "gambling"
                        matched_reasons = [domain_signal, "domain_anchor_preserved_dead_lander"]
                    else:
                        final_status = "dead"
                        matched_reasons = [fetch_res.error or "Connection failed / 404"]
                else:
                    # Page is LIVE -> Run full multi-signal & AI classification
                    providers = detect_igaming_providers(html)
                    funnels = detect_gambling_funnels(html)

                    if providers:
                        is_gambling = True
                        final_status = "gambling"
                        matched_reasons = [f"provider:{p}" for p in providers]
                    elif is_g_domain:
                        is_gambling = True
                        final_status = "gambling"
                        matched_reasons = [domain_signal]
                    else:
                        # Heuristic + AI Challenge
                        decision, heuristic_reasons = classify(html, url=url, keywords=keywords)
                        if decision == "gambling":
                            is_gambling = True
                            final_status = "gambling"
                            matched_reasons = heuristic_reasons
                        else:
                            # Escalate to AI Challenge
                            ai_res = await classify_with_challenge(html, url=url, matched_keywords=heuristic_reasons)
                            ai_verdict = ai_res.get("verdict", "unconfirmed")

                            if ai_verdict == "gambling":
                                is_gambling = True
                                final_status = "gambling"
                                matched_reasons = list(set(heuristic_reasons + ai_res.get("key_triggers", [])))
                                if not matched_reasons and ai_res.get("reason"):
                                    matched_reasons = [ai_res["reason"]]
                            elif ai_verdict == "regular":
                                final_status = "regular"
                                matched_reasons = [ai_res.get("reason", "AI challenge verified regular")]
                            else:
                                # Timeout / unconfirmed
                                if heuristic_reasons:
                                    is_gambling = True
                                    final_status = "gambling"
                                    matched_reasons = heuristic_reasons
                                else:
                                    final_status = "unconfirmed"
                                    matched_reasons = ["ollama_timeout_no_signals"]

                # ── 3. Apply Restoration & DB Upsert ───────────────────────
                if is_gambling:
                    dest = SCREENSHOTS_DIR / fn
                    if img_path.exists():
                        try:
                            shutil.move(str(img_path), str(dest))
                        except Exception:
                            pass
                    write_result(
                        domain,
                        url=url,
                        status="gambling",
                        reason=matched_reasons,
                        screenshot_taken=True,
                    )
                    async with stats_lock:
                        stats["restored_gambling"] += 1
                else:
                    if final_status == "regular":
                        async with stats_lock:
                            stats["kept_regular"] += 1
                    elif final_status == "blocked":
                        async with stats_lock:
                            stats["kept_blocked"] += 1
                    elif final_status == "dead":
                        async with stats_lock:
                            stats["kept_dead"] += 1
                    else:
                        async with stats_lock:
                            stats["unconfirmed"] += 1

                    write_result(
                        domain,
                        url=url,
                        status=final_status,
                        reason=matched_reasons,
                        screenshot_taken=False,
                    )

                async with stats_lock:
                    pbar.update(1)
                    pbar.set_postfix({
                        "Restored": stats["restored_gambling"],
                        "Regular": stats["kept_regular"],
                        "Dead/Block": stats["kept_dead"] + stats["kept_blocked"]
                    })
            except Exception as e:
                async with stats_lock:
                    pbar.update(1)
                    print(f"\n[recheck_fp ERROR] {fn}: {e}", flush=True)

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.gather(*workers)
    pbar.close()
    await close_ai_session()

    print("\n" + "=" * 65)
    print("      REMOVED SCREENSHOTS AI RE-EVALUATION COMPLETE       ")
    print("=" * 65)
    print(f" Total Screenshots Checked       : {len(files)}")
    print(f"  * Confirmed Gambling (RESTORED) : {stats['restored_gambling']} (Moved to output/screenshots/)")
    print(f"  * Kept in Removed Folder        : {len(files) - stats['restored_gambling']}")
    print(f"    - Verified Regular Sites      : {stats['kept_regular']}")
    print(f"    - Dead / 404 / Connection Err : {stats['kept_dead']}")
    print(f"    - Blocked (403 / WAF)         : {stats['kept_blocked']}")
    print(f"    - Unconfirmed (Timeout)       : {stats['unconfirmed']}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(recheck_fp_removed(concurrency=12))
