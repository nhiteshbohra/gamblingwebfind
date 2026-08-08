import asyncio
from core.discovery import search_keyword
from core.dedup import normalize_url, extract_domain, is_duplicate
from core.liveness import run_liveness_batch
from orchestrator.worker import process_url

async def run_discovery_cycle(db, settings):
    keywords = await db.get_next_keywords(limit=10)
    if not keywords:
        return

    searxng_url = settings.get('searxng_base_url', 'http://127.0.0.1:8080')
    max_pages = settings.get('max_pages_per_keyword', 4)
    delay = settings.get('searxng_page_delay_seconds', 0.5)

    async def _process_kw(kw):
        kw_id = kw['id']
        term = kw['term']
        urls = await search_keyword(term, keyword_id=kw_id, db=db,
                                    searxng_base_url=searxng_url, max_pages=max_pages, delay=delay)
        for raw_url in urls:
            norm_url = normalize_url(raw_url)
            if not await is_duplicate(norm_url, db):
                domain = extract_domain(norm_url)
                await db.upsert_url(norm_url, domain, kw_id)

    tasks = [_process_kw(kw) for kw in keywords]
    await asyncio.gather(*tasks, return_exceptions=True)

async def run_worker_cycle(db, settings):
    concurrency = settings.get('max_concurrent_fetches', 20)
    pending_urls = await db.get_pending_urls(limit=concurrency)
    if not pending_urls:
        return
    tasks = [process_url(row, db, settings) for row in pending_urls]
    await asyncio.gather(*tasks, return_exceptions=True)

async def _liveness_timer(db, settings):
    """Runs liveness batch every liveness_recheck_interval_hours. Fires independently."""
    interval_hours = settings.get('liveness_recheck_interval_hours', 72)
    interval_seconds = interval_hours * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        print(f"[Liveness] Starting batch re-check...")
        await run_liveness_batch(db, settings)
        print(f"[Liveness] Batch re-check complete.")

async def start_scheduler(db, settings):
    # Start liveness timer as a background task (first fire after interval, not immediately)
    asyncio.create_task(_liveness_timer(db, settings))

    while True:
        try:
            await run_discovery_cycle(db, settings)
            await run_worker_cycle(db, settings)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Scheduler Error] {e}")
            await asyncio.sleep(10)
