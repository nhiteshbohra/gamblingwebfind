"""
checking_url/fetcher.py — High-performance AsyncFetcher with TLS/browser impersonation
and failure classification.
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

from scrapling.fetchers import AsyncFetcher

IMPERSONATE = "chrome"

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
    """Return failure_type string."""
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
    """Turn a scrapling Response into a FetchResult, applying body-marker sniffing."""
    if resp is None:
        return FetchResult(url=url, latency=latency, failure_type='connection_failed')
    
    status = getattr(resp, "status", 0)
    raw_body = getattr(resp, "body", None)
    if isinstance(raw_body, bytes):
        html = raw_body.decode(getattr(resp, "encoding", "utf-8") or "utf-8", errors="ignore")
    elif isinstance(raw_body, str):
        html = raw_body
    else:
        html = ""

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


async def fetch(url: str, domain_id: str, timeout_seconds=10, per_domain_delay=2.0, retries=2) -> FetchResult:
    """Fetch a URL using AsyncFetcher with TLS/browser impersonation."""
    start = asyncio.get_running_loop().time()
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
        latency = asyncio.get_running_loop().time() - start
        return FetchResult(url=url, latency=latency, error=str(e), failure_type='connection_failed')

    latency = asyncio.get_running_loop().time() - start
    return _result_from_response(url, resp, latency)

