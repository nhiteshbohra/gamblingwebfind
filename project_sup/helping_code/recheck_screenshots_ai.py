"""
project_sup/helping_code/recheck_screenshots_ai.py — Re-check output/screenshots/ images with AI Model.

Workflow:
1. Scan output/screenshots/ for .jpg and .png images.
2. Match each image file to its domain/URL using MongoDB checked_domains & _url_to_filename.
3. Live fetch HTML for each domain using Scrapling fetcher.
4. Run Layer 2 Heuristic Pre-screen & Layer 3 Ollama 2-Round AI Challenge Classifier.
5. If verdict == 'gambling':
   - Keep screenshot in output/screenshots/
   - Update MongoDB checked_domains (status="gambling", screenshot_taken=True)
6. If verdict != 'gambling' (regular, dead, blocked, unconfirmed):
   - Move screenshot from output/screenshots/ to output/screenshots_fp_removed/
   - Update MongoDB checked_domains (status=verdict, screenshot_taken=False)
7. Display live progress and summary statistics.
"""
import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

# Silence verbose loggers for clean progress display
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright", "ai_classifier"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(dotenv_path=ROOT_DIR / ".env")

from db.mongo_client import get_db, checked_domains, write_result
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify
from checking_url.ai_classifier import classify_with_challenge, check_ollama_status, close_ai_session
from capture_url.screenshot import _url_to_filename

SCREENSHOTS_DIR = ROOT_DIR / "output" / "screenshots"
FP_REMOVED_DIR = ROOT_DIR / "output" / "screenshots_fp_removed"


def build_filename_to_doc_map() -> dict:
    """Build a mapping from screenshot filename -> MongoDB checked_domains doc."""
    get_db()
    mapping = {}
    for doc in checked_domains().find():
        domain = doc.get("domain") or doc.get("_id")
        url = doc.get("url") or f"https://{domain}"
        if not domain:
            continue
        
        # Try different URL variations to match possible generated filenames
        fn1 = _url_to_filename(url)
        fn2 = _url_to_filename(f"https://{domain}")
        fn3 = _url_to_filename(f"http://{domain}")
        
        mapping[fn1] = doc
        mapping[fn2] = doc
        mapping[fn3] = doc
    return mapping


def infer_domain_from_filename(filename: str) -> str:
    """Fallback helper to extract domain from filename e.g. '101-gamee_net_57e8b32d.jpg' -> '101-gamee.net'."""
    name_without_ext = os.path.splitext(filename)[0]
    # Strip hash if present (e.g., _57e8b32d)
    parts = name_without_ext.split('_')
    if len(parts) > 1 and len(parts[-1]) == 8 and all(c in '0123456789abcdefABCDEF' for c in parts[-1]):
        parts = parts[:-1]
    
    # Reconstruct domain by converting last segment suffix or replacing underscores
    clean_str = "_".join(parts)
    # Common TLD replaces e.g. _com -> .com, _net -> .net, _org -> .org, _co_in -> .co.in
    clean_str = re.sub(r'_([a-z0-9]+)$', r'.\1', clean_str)
    clean_str = clean_str.replace('_', '.')
    return clean_str


async def recheck_screenshots(concurrency: int = None, limit: int = 0):
    get_db()
    
    # Check Ollama AI status
    ollama_ok, ollama_msg = await check_ollama_status()
    print(f"\n[recheck] Local AI Status: {ollama_msg}")
    if not ollama_ok:
        print("[!] WARNING: Ollama AI is not fully accessible. Non-gambling sites will default to 'unconfirmed'.")
        ans = input("Do you want to continue anyway? (y/N): ").strip().lower()
        if ans != 'y':
            print("Aborted.")
            return

    if not SCREENSHOTS_DIR.exists():
        print(f"[!] Directory {SCREENSHOTS_DIR} does not exist.")
        return

    os.makedirs(FP_REMOVED_DIR, exist_ok=True)

    image_files = list(SCREENSHOTS_DIR.glob("*.jpg")) + list(SCREENSHOTS_DIR.glob("*.png"))
    if limit > 0:
        image_files = image_files[:limit]

    total_images = len(image_files)
    print(f"[recheck] Found {total_images} screenshot(s) in {SCREENSHOTS_DIR.relative_to(ROOT_DIR)} to re-check.")

    if total_images == 0:
        return

    # Build mapping from database
    fn_map = build_filename_to_doc_map()
    keywords = load_keywords()
    
    concurrency = concurrency or int(os.getenv("CHECK_CONCURRENCY", os.getenv("AI_CONCURRENCY", 10)))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 1.5))
    sem = asyncio.Semaphore(concurrency)

    stats = {
        "total": total_images,
        "kept_gambling": 0,
        "moved_regular": 0,
        "moved_dead": 0,
        "moved_blocked": 0,
        "moved_unconfirmed": 0,
        "ai_evaluated": 0,
        "challenge_overrides": 0,
    }

    pbar = tqdm(total=total_images, desc="AI Re-checking Screenshots", unit="img", dynamic_ncols=True)
    pbar.set_postfix({"Kept": 0, "Moved": 0})

    stats_lock = asyncio.Lock()

    async def process_image(img_path: Path):
        try:
            fn = img_path.name
            doc = fn_map.get(fn)
            
            if doc:
                domain = doc.get("domain") or doc.get("_id")
                url = doc.get("url") or f"https://{domain}"
            else:
                domain = infer_domain_from_filename(fn)
                url = f"https://{domain}"

            # 1. Fetch live HTML
            async with sem:
                result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

            if result.failure_type:
                status_map = {"blocked": "blocked", "dead_confirmed": "dead", "connection_failed": "dead"}
                final_status = status_map.get(result.failure_type, "dead")
                
                # Move screenshot out of active folder
                dest_path = FP_REMOVED_DIR / fn
                if img_path.exists():
                    try:
                        shutil.move(str(img_path), str(dest_path))
                    except Exception:
                        pass
                
                write_result(domain, url=url, status=final_status, reason=[result.failure_type], screenshot_taken=False)
                
                async with stats_lock:
                    if final_status == "blocked":
                        stats["moved_blocked"] += 1
                    else:
                        stats["moved_dead"] += 1
                    pbar.update(1)
                    pbar.set_postfix({
                        "Kept": stats["kept_gambling"],
                        "Moved": stats["moved_regular"] + stats["moved_dead"] + stats["moved_blocked"] + stats["moved_unconfirmed"]
                    })
                return

            # 2. Heuristic Pre-screen & AI Challenge Classifier
            decision, matched_reasons = classify(result.html or "", url=url, keywords=keywords)
            final_status = "regular"
            final_reasons = matched_reasons

            if decision == "gambling":
                final_status = "gambling"
                async with stats_lock:
                    stats["kept_gambling"] += 1
            elif decision == "needs_ai":
                async with stats_lock:
                    stats["ai_evaluated"] += 1
                ai_res = await classify_with_challenge(
                    result.html or "",
                    url=url,
                    matched_keywords=matched_reasons
                )
                ai_verdict = ai_res.get("verdict", "unconfirmed")

                if ai_verdict == "gambling":
                    final_status = "gambling"
                    final_reasons = list(set(matched_reasons + ai_res.get("key_triggers", [])))
                    if not final_reasons and ai_res.get("reason"):
                        final_reasons = [ai_res["reason"]]
                    async with stats_lock:
                        stats["kept_gambling"] += 1
                        if ai_res.get("challenge_override"):
                            stats["challenge_overrides"] += 1
                elif ai_verdict == "regular":
                    final_status = "regular"
                    final_reasons = [ai_res.get("reason", "AI challenge verified regular")]
                    async with stats_lock:
                        stats["moved_regular"] += 1
                else:
                    final_status = "unconfirmed"
                    final_reasons = ["ollama_offline_or_timeout"]
                    async with stats_lock:
                        stats["moved_unconfirmed"] += 1
            else:
                final_status = "regular"
                async with stats_lock:
                    stats["moved_regular"] += 1

            # 3. Apply file movement and MongoDB updates
            if final_status == "gambling":
                # Confirmed gambling -> keep screenshot in output/screenshots/
                write_result(domain, url=url, status="gambling", reason=final_reasons, screenshot_taken=True)
            elif final_status == "unconfirmed":
                # Timeout / unconfirmed -> KEEP screenshot in output/screenshots/ for safety!
                write_result(domain, url=url, status="unconfirmed", reason=final_reasons, screenshot_taken=True)
                async with stats_lock:
                    stats["kept_gambling"] += 1
            else:
                # Verified regular, dead, or blocked -> Move to output/screenshots_fp_removed/
                dest_path = FP_REMOVED_DIR / fn
                if img_path.exists():
                    try:
                        shutil.move(str(img_path), str(dest_path))
                    except Exception:
                        pass
                write_result(domain, url=url, status=final_status, reason=final_reasons, screenshot_taken=False)

            async with stats_lock:
                pbar.update(1)
                pbar.set_postfix({
                    "Kept": stats["kept_gambling"],
                    "Moved": stats["moved_regular"] + stats["moved_dead"] + stats["moved_blocked"]
                })
        except Exception as e:
            async with stats_lock:
                pbar.update(1)
                print(f"\n[recheck ERROR] Error processing {img_path.name}: {e}")

    # Bounded worker queue to prevent unconstrained task allocation
    queue = asyncio.Queue()
    for f in image_files:
        queue.put_nowait(f)

    async def worker():
        while not queue.empty():
            try:
                img_path = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process_image(img_path)
            finally:
                queue.task_done()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
    finally:
        await close_ai_session()
        pbar.close()

    total_moved = stats["moved_regular"] + stats["moved_dead"] + stats["moved_blocked"] + stats["moved_unconfirmed"]
    print("\n" + "=" * 65)
    print("        SCREENSHOTS AI RE-CHECK COMPLETE SUMMARY         ")
    print("=" * 65)
    print(f" Total Screenshots Examined       : {stats['total']:,}")
    print(f"  * Confirmed Gambling (KEPT)      : {stats['kept_gambling']:,}")
    print(f"  * False Positives / Non-Gambling : {total_moved:,} (Moved to output/screenshots_fp_removed/)")
    print(f"    - Verified Regular Sites       : {stats['moved_regular']:,}")
    print(f"    - Dead / Unreachable           : {stats['moved_dead']:,}")
    print(f"    - Blocked (403 / WAF)          : {stats['moved_blocked']:,}")
    if stats["moved_unconfirmed"]:
        print(f"    - Unconfirmed (Ollama Down)    : {stats['moved_unconfirmed']:,}")
    print(f"  * AI Evaluated Domains           : {stats['ai_evaluated']:,}")
    print(f"  * Challenge Round Overrides      : {stats['challenge_overrides']:,}")
    print("=" * 65 + "\n")

    return stats


def main():
    concurrency = int(os.getenv("CHECK_CONCURRENCY", 10))
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    asyncio.run(recheck_screenshots(concurrency=concurrency, limit=limit))


if __name__ == "__main__":
    main()
