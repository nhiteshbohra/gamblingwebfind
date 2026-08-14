"""
checking_url/fetcher.py — Scrapling-backed fetcher with retry, backoff, a
Cloudflare-solving escalation tier, and failure classification.

Rate-limit bookkeeping uses mongo_client.get_checked() / checked_domains().update_one()
instead of the old SQLite db.get_last_domain_fetch_time() / db.touch_domain().

Two tiers:
  1. Fast tier   — scrapling.fetchers.AsyncFetcher (curl_cffi, TLS/browser
                   impersonation). Cheap, no browser, used for every domain.
  2. Stealth tier — scrapling.fetchers.StealthyFetcher (real stealth browser,
                    solve_cloudflare=True). Only triggered when the fast tier
                    comes back "blocked", and only if STEALTH_FALLBACK=true.
"""
import asyncio
import logging
import os

# Silence verbose third-party loggers to prevent breaking tqdm progress bar
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

from scrapling.fetchers import AsyncFetcher, StealthyFetcher

# scrapling's AsyncFetcher generates real, matching browser headers itself when
# impersonate/stealthy_headers are set, so we no longer hand-roll a UA list.
IMPERSONATE = "chrome"

# The stealth tier launches a real browser per call, which is far heavier than
# the fast tier. Cap how many run at once, independent of MAX_CONCURRENT_FETCHES,
# so a batch of "blocked" domains can't spin up 20 browsers simultaneously.
_stealth_sem = asyncio.Semaphore(int(os.getenv("STEALTH_CONCURRENCY", 3)))

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


def _result_from_response(url: str, resp, latency: float) -> FetchResult:
    """Turn a scrapling Response into a FetchResult, applying the same
    body-marker sniffing the old aiohttp path used."""
    status = resp.status
    try:
        html = resp.body.decode(resp.encoding or 'utf-8', errors='ignore')
    except Exception:
        html = ''

    if status >= 400 or not html.strip():
        ft = _classify_failure(status_code=status, html=html)
        return FetchResult(url=url, status_code=status, latency=latency, failure_type=ft)

    result = FetchResult(url=url, status_code=status, html=html, latency=latency)
    body = html[:5000].lower()
    if any(m in body for m in _BLOCKED_BODY_MARKERS):
        result.html = None
        result.failure_type = 'blocked'
    elif any(m in body for m in _PARKED_BODY_MARKERS):
        result.html = None
        result.failure_type = 'dead_confirmed'
    return result


async def _fetch_fast(url: str, timeout_seconds: float, retries: int) -> FetchResult:
    """Tier 1: scrapling AsyncFetcher — curl_cffi TLS/header impersonation,
    no browser. Cheap enough to run on every domain."""
    start = asyncio.get_event_loop().time()
    try:
        resp = await AsyncFetcher.get(
            url,
            timeout=timeout_seconds,
            impersonate=IMPERSONATE,
            stealthy_headers=True,
            follow_redirects=True,
            retries=retries,
        )
    except Exception as e:
        latency = asyncio.get_event_loop().time() - start
        return FetchResult(url=url, latency=latency, error=str(e), failure_type='connection_failed')

    latency = asyncio.get_event_loop().time() - start
    return _result_from_response(url, resp, latency)


async def _fetch_stealth(url: str, timeout_seconds: float) -> FetchResult:
    """Tier 2: scrapling StealthyFetcher — real stealth browser that actively
    solves Cloudflare Turnstile/Interstitial challenges. Only called as a
    fallback for domains the fast tier marked 'blocked'."""
    start = asyncio.get_event_loop().time()
    async with _stealth_sem:
        try:
            resp = await StealthyFetcher.async_fetch(
                url,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=max(timeout_seconds, 60) * 1000,  # ms; CF solving needs >= 60s
            )
        except Exception as e:
            latency = asyncio.get_event_loop().time() - start
            return FetchResult(url=url, latency=latency, error=str(e), failure_type='connection_failed')

    latency = asyncio.get_event_loop().time() - start
    return _result_from_response(url, resp, latency)


async def fetch(url: str, domain_id: str, timeout_seconds=10, per_domain_delay=2.0, retries=2) -> FetchResult:
    """Fetch a URL, running fast tier first, escalating to stealth if blocked.

    Runs the fast (non-browser) tier first. If that tier reports the domain
    as 'blocked' and STEALTH_FALLBACK is enabled, escalates once to the
    stealth browser tier, which can actually solve Cloudflare challenges
    instead of just detecting them.
    """
    result = await _fetch_fast(url, timeout_seconds, retries)

    stealth_enabled = os.getenv("STEALTH_FALLBACK", "false").strip().lower() == "true"
    if result.failure_type == 'blocked' and stealth_enabled:
        stealth_timeout = float(os.getenv("STEALTH_TIMEOUT", 60))
        result = await _fetch_stealth(url, stealth_timeout)

    return result
