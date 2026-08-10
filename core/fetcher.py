import asyncio
import random
from datetime import datetime, timezone
import aiohttp

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"
]

# Markers in response body that indicate bot/CAPTCHA blocking (Cloudflare et al.)
_BLOCKED_BODY_MARKERS = [
    "checking your browser", "cf-challenge", "captcha", "just a moment",
    "enable javascript and cookies", "cf_chl_opt", "ray id",
]

# Markers indicating a parked/for-sale domain (dead_confirmed even if HTTP 200)
_PARKED_BODY_MARKERS = [
    "this domain is for sale", "domain for sale", "buy this domain",
    "parked by", "domain parking", "sedo.com", "dan.com/domain",
    "godaddy.com/domains", "afternic.com",
]


def _classify_failure(status_code=None, html=None, error=None):
    """Return a failure_type string for a non-successful fetch.

    connection_failed  — DNS/timeout/refused, no response
    blocked            — HTTP 403/429, or Cloudflare/CAPTCHA body
    dead_confirmed     — HTTP 404, or parked-domain body on a 200

    ponytail: ceiling = we don't handle every edge case (e.g. 503 could be
    temporary or permanent). 503 → connection_failed (retry-worthy).
    Upgrade path: add status_code ranges to blocked/dead as false-positives
    are confirmed in production.
    """
    if error is not None:
        return 'connection_failed'

    if status_code in (403, 429):
        return 'blocked'

    if status_code == 404:
        return 'dead_confirmed'

    if html:
        body = html[:5000].lower()  # only check preamble, cheap
        if any(m in body for m in _BLOCKED_BODY_MARKERS):
            return 'blocked'
        if any(m in body for m in _PARKED_BODY_MARKERS):
            return 'dead_confirmed'

    # All other HTTP errors (4xx except 403/404/429, 5xx) → connection_failed
    # so they get the slow-retry treatment rather than being permanently dead
    return 'connection_failed'


class FetchResult:
    def __init__(self, url, status_code=None, html=None, latency=0.0, error=None, failure_type=None):
        self.url = url
        self.status_code = status_code
        self.html = html
        self.latency = latency
        self.error = error
        # failure_type is None on success; one of 'connection_failed'/'blocked'/'dead_confirmed' on failure
        self.failure_type = failure_type


async def fetch(url: str, domain: str, db, timeout_seconds=10, per_domain_delay=2.0, retries=2) -> FetchResult:
    # Per-domain rate limiting: delay if domain was fetched too recently
    if per_domain_delay > 0:
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
                    raw_bytes = await resp.content.read(500000)
                    html = raw_bytes.decode('utf-8', errors='ignore')
                    if resp.status >= 400 or not html.strip():
                        ft = _classify_failure(status_code=resp.status, html=html)
                        result = FetchResult(url=url, status_code=resp.status, latency=latency, failure_type=ft)
                        break
                    result = FetchResult(url=url, status_code=resp.status, html=html, latency=latency)
                    # Check for bot-block even on 200 (Cloudflare returns 200 on challenge pages)
                    body = html[:5000].lower()
                    if any(m in body for m in _BLOCKED_BODY_MARKERS):
                        result.html = None
                        result.failure_type = 'blocked'
                    elif any(m in body for m in _PARKED_BODY_MARKERS):
                        result.html = None
                        result.failure_type = 'dead_confirmed'
                    break
        except Exception as e:
            latency = asyncio.get_event_loop().time() - start_time
            if attempt == retries:
                result = FetchResult(url=url, latency=latency, error=str(e),
                                     failure_type='connection_failed')
            else:
                await asyncio.sleep(1.0 * (2 ** attempt))

    # Always update domain rate-limit bookkeeping regardless of outcome
    await db.touch_domain(domain)
    return result
