import asyncio
import aiohttp
from bs4 import BeautifulSoup

PARKED_INDICATORS = [
    "domain for sale", "this domain is parked", "buy this domain",
    "domain name is available", "under construction", "renew your domain"
]

async def check_liveness(url: str, timeout_seconds=5) -> bool:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status < 200 or resp.status >= 400:
                    return False
                html = await resp.text(errors='ignore')
                text = BeautifulSoup(html, 'html.parser').get_text().lower()
                for indicator in PARKED_INDICATORS:
                    if indicator in text:
                        return False
                return True
    except Exception:
        return False


async def run_liveness_batch(db, settings):
    """Pull all verified rows, re-check liveness, flip dead ones to 'dead'."""
    timeout = settings.get('fetch_timeout_seconds', 10)
    rows = await db.get_verified_live_rows()
    for row in rows:
        url = row['url']
        alive = await check_liveness(url, timeout_seconds=timeout)
        if alive:
            # Update last_checked_at without changing status
            await db.mark_status_liveness(url, alive=True)
        else:
            # Find the url_id to mark dead
            url_id = await db.get_url_id(url)
            if url_id:
                await db.mark_status(url_id, 'dead', reasons=['liveness_check_failed'])
