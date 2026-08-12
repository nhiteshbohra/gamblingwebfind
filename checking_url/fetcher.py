"""
checking_url/fetcher.py — HTTP fetcher with retry, backoff, and failure classification.

Rate-limit bookkeeping now uses mongo_client.get_domain() / domains().update_one()
instead of the old SQLite db.get_last_domain_fetch_time() / db.touch_domain().
"""
import asyncio
import random
from datetime import datetime, timezone

import aiohttp

from db.mongo_client import get_checked, checked_domains

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

_BLOCKED_BODY_MARKERS = [
    "checking your browser", "cf-challenge", "captcha", "just a moment",
    "enable javascript and cookies", "cf_chl_opt", "ray id",
]

_PARKED_BODY_MARKERS = [
    "this domain is for sale", "domain for sale", "buy this domain",
    "parked by", "domain parking", "sedo.com", "dan.com/domain",
    "godaddy.com/domains", "afternic.com",
]


def _classify_failure(status_code=None, html=None, error=None):
    """Return failure_type string.

    ponytail: ceiling = 503 maps to connection_failed (retry-worthy) even when
    it might be permanent. Upgrade path: add status ranges as false-positives
    are confirmed in production.
    """
    if error is not None:
        return 'connection_failed'
    if status_code in (403, 429):
        return 'blocked'
    if status_code == 404:
        return 'dead_confirmed'
    if html:
        body = html[:5000].lower()
        if any(m in body for m in _BLOCKED_BODY_MARKERS):
            return 'blocked'
        if any(m in body for m in _PARKED_BODY_MARKERS):
            return 'dead_confirmed'
    return 'connection_failed'


class FetchResult:
    def __init__(self, url, status_code=None, html=None, latency=0.0, error=None, failure_type=None):
        self.url = url
        self.status_code = status_code
        self.html = html
        self.latency = latency
        self.error = error
        self.failure_type = failure_type  # None = success


async def fetch(url: str, domain_id: str, timeout_seconds=10, per_domain_delay=2.0, retries=2) -> FetchResult:
    """Fetch a URL with per-domain rate limiting via Mongo last_updated_at."""
    if per_domain_delay > 0:
        doc = get_checked(domain_id)
        if doc and doc.get('last_updated_at'):
            try:
                last_dt = datetime.fromisoformat(doc['last_updated_at'])
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < per_domain_delay:
                    await asyncio.sleep(per_domain_delay - elapsed)
            except (ValueError, TypeError):
                pass

    headers = {'User-Agent': random.choice(USER_AGENTS)}
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    result = None

    for attempt in range(retries + 1):
        start = asyncio.get_event_loop().time()
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    latency = asyncio.get_event_loop().time() - start
                    raw_bytes = await resp.content.read(500_000)
                    html = raw_bytes.decode('utf-8', errors='ignore')
                    if resp.status >= 400 or not html.strip():
                        ft = _classify_failure(status_code=resp.status, html=html)
                        result = FetchResult(url=url, status_code=resp.status, latency=latency, failure_type=ft)
                        break
                    result = FetchResult(url=url, status_code=resp.status, html=html, latency=latency)
                    body = html[:5000].lower()
                    if any(m in body for m in _BLOCKED_BODY_MARKERS):
                        result.html = None
                        result.failure_type = 'blocked'
                    elif any(m in body for m in _PARKED_BODY_MARKERS):
                        result.html = None
                        result.failure_type = 'dead_confirmed'
                    break
        except Exception as e:
            latency = asyncio.get_event_loop().time() - start
            if attempt == retries:
                result = FetchResult(url=url, latency=latency, error=str(e), failure_type='connection_failed')
            else:
                await asyncio.sleep(1.0 * (2 ** attempt))

    checked_domains().update_one(
        {"_id": domain_id},
        {"$set": {"last_updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return result
