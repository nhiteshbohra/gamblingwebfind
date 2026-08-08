import asyncio
import random
from datetime import datetime, timezone
import aiohttp

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"
]


class FetchResult:
    def __init__(self, url, status_code=None, html=None, latency=0.0, error=None):
        self.url = url
        self.status_code = status_code
        self.html = html
        self.latency = latency
        self.error = error


async def fetch(url: str, domain: str, db, timeout_seconds=10, per_domain_delay=2.0, retries=2) -> FetchResult:
    # Per-domain rate limiting: delay if domain was fetched too recently
    last_fetched = await db.get_last_domain_fetch_time(domain)
    if last_fetched:
        try:
            last_dt = datetime.fromisoformat(last_fetched)
            # SQLite CURRENT_TIMESTAMP is UTC naive; make comparable
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if elapsed < per_domain_delay:
                await asyncio.sleep(per_domain_delay - elapsed)
        except (ValueError, TypeError):
            pass  # Malformed timestamp; proceed without delay

    headers = {'User-Agent': random.choice(USER_AGENTS)}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    result = None
    for attempt in range(retries + 1):
        start_time = asyncio.get_event_loop().time()
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    latency = asyncio.get_event_loop().time() - start_time
                    if resp.status >= 400:
                        # HTTP error: record and return immediately — do not retry
                        result = FetchResult(url=url, status_code=resp.status, latency=latency)
                        break
                    raw_bytes = await resp.content.read(500000)
                    html = raw_bytes.decode('utf-8', errors='ignore')
                    result = FetchResult(url=url, status_code=resp.status, html=html, latency=latency)
                    break
        except Exception as e:
            latency = asyncio.get_event_loop().time() - start_time
            if attempt == retries:
                result = FetchResult(url=url, latency=latency, error=str(e))
            else:
                await asyncio.sleep(1.0 * (2 ** attempt))

    # Always update domain rate-limit bookkeeping regardless of outcome
    await db.touch_domain(domain)
    return result
