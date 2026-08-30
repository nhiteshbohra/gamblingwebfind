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

# Reuse the single canonical parked/for-sale marker list from classifier.py instead of
# keeping a second, shorter, independently-maintained copy here. Previously this file had
# its own short list (10 markers) while classifier.py had a much larger one (40+ markers)
# used later in the pipeline — meaning a parked page whose marker was only in the *larger*
# list would slip past this pre-classification check, reach the AI stage, and (before the
# domain-anchor fix) could get force-locked to "gambling" purely because the domain name
# contained a gambling-adjacent token. Importing the same list here means parked pages are
# now caught as early and consistently as possible, before they ever reach the classifier.
try:
    from checking_url.classifier import PARKED_AND_FOR_SALE_MARKERS as _PARKED_BODY_MARKERS
except ImportError:
    # Fallback if imported standalone / circular import issue — keep a minimal safety net.
    _PARKED_BODY_MARKERS = [
        "this domain is for sale", "domain for sale", "buy this domain",
        "parked by", "domain parking", "sedo.com", "dan.com/domain",
        "godaddy.com/domains", "afternic.com",
    ]

IMPERSONATE = "chrome"

# Borrowed from PySecAuditWebScanner (a third-party security-scanner project reviewed
# for this codebase) -- it detects gambling-content cloaking by diffing what a normal
# browser-looking fetch sees vs. what a generic/bot-like fetch sees. fetch() above only
# ever uses a single Chrome-impersonation profile, so a site that deliberately serves
# clean content to that exact fingerprint while showing gambling content to ordinary
# visitors (or vice versa) previously had zero chance of being caught. This is a
# best-effort SUPPLEMENTARY signal only -- see its call site in runner.py, which never
# lets this override a verdict by itself, only flags it for human review. Added 2026-08-24.
_BOT_LIKE_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


async def check_cloaking(url: str, timeout_seconds: int = 10, gambling_keywords=None) -> dict:
    """Fetch `url` twice with two different request signatures -- once matching this
    pipeline's normal browser-impersonating fetch, once with a generic/bot-like User-Agent
    and no stealthy-header spoofing -- and compare the results for gambling-keyword
    presence and gross content-length divergence.

    Never raises: any fetch failure just means cloaking is undetermined, since this is a
    corroborating signal, not a primary classification path. Callers should treat a
    True `cloaking_suspected` as "worth a human look", never as proof by itself.
    """
    gambling_keywords = gambling_keywords or set()

    async def _fetch_as(bot_like: bool):
        kwargs = dict(
            timeout=timeout_seconds,
            impersonate=IMPERSONATE,
            stealthy_headers=(not bot_like),
            follow_redirects=True,
            retries=1,
        )
        if bot_like:
            kwargs["headers"] = {"User-Agent": _BOT_LIKE_UA}
        try:
            resp = await AsyncFetcher.get(url, **kwargs)
        except TypeError:
            # Older/incompatible Scrapling version without a `headers` kwarg -- fall back
            # to disabling stealthy_headers as the only available differentiator.
            kwargs.pop("headers", None)
            kwargs["stealthy_headers"] = False
            try:
                resp = await AsyncFetcher.get(url, **kwargs)
            except Exception:
                return None
        except Exception:
            return None
        raw_body = getattr(resp, "body", None) if resp is not None else None
        if isinstance(raw_body, bytes):
            return raw_body.decode(getattr(resp, "encoding", "utf-8") or "utf-8", errors="ignore")
        return raw_body if isinstance(raw_body, str) else ""

    browser_html = await _fetch_as(bot_like=False)
    bot_html = await _fetch_as(bot_like=True)

    if browser_html is None or bot_html is None:
        return {
            "cloaking_suspected": False,
            "browser_len": len(browser_html or ""),
            "bot_len": len(bot_html or ""),
            "note": "one or both fetches failed; cloaking undetermined",
        }

    b_low, o_low = browser_html.lower(), bot_html.lower()
    b_has_kw = any(kw in b_low for kw in gambling_keywords)
    o_has_kw = any(kw in o_low for kw in gambling_keywords)
    longer = max(len(browser_html), len(bot_html))
    len_ratio = (min(len(browser_html), len(bot_html)) / longer) if longer else 1.0

    suspected = False
    note = "no significant difference between browser and bot-like fetch"
    if gambling_keywords and (b_has_kw != o_has_kw):
        suspected = True
        note = f"gambling keyword presence differs: browser={b_has_kw}, bot-like={o_has_kw}"
    elif len_ratio < 0.5:
        suspected = True
        note = f"content length differs sharply between fetches (ratio={len_ratio:.2f})"

    return {
        "cloaking_suspected": suspected,
        "browser_len": len(browser_html),
        "bot_len": len(bot_html),
        "note": note,
    }

_BLOCKED_BODY_MARKERS = [
    "checking your browser", "cf-challenge", "captcha", "just a moment",
    "enable javascript and cookies", "cf_chl_opt", "ray id",
]


def _classify_failure(status_code=None, html=None, error=None):
    """Return failure_type string.

    'parked' (domain-for-sale / registrar parking lander) is intentionally distinct from
    'dead_confirmed' (true 404 / no live content at all) — a parked-for-sale domain is
    reachable and rendering *something*, it's just not gambling and not dead, so the
    caller treats it as 'regular' rather than 'dead'. Body-marker checks run before the
    plain 404 fallback so a 404 page that still renders parking-lander copy is classified
    as 'parked', not 'dead_confirmed'.
    """
    if error is not None:
        return 'connection_failed'
    if status_code in (403, 429):
        return 'blocked'
    if status_code is not None and 500 <= status_code < 600:
        # Transient server-side errors (maintenance, overload, bad gateway) are not
        # proof the domain is dead — lumping them in with 404/connection failures
        # permanently freezes a possibly-live gambling site into the 'dead' bucket
        # (dead domains are not auto-rechecked). Give them their own category so the
        # caller can retry before giving up.
        return 'server_error'
    if html:
        body = html[:5000].lower()
        if any(m in body for m in _BLOCKED_BODY_MARKERS):
            return 'blocked'
        if any(m in body for m in _PARKED_BODY_MARKERS):
            return 'parked'
    if status_code == 404:
        return 'dead_confirmed'
    return 'connection_failed'


class FetchResult:
    def __init__(self, url, status_code=None, html=None, latency=0.0, error=None, failure_type=None, final_url=None):
        self.url = url
        self.final_url = final_url or url
        self.status_code = status_code
        self.html = html
        self.latency = latency
        self.error = error
        self.failure_type = failure_type  # None = success
        self.redirected = bool(final_url and final_url.rstrip("/").lower() != url.rstrip("/").lower())


def _result_from_response(url: str, resp, latency: float) -> FetchResult:
    """Turn a scrapling Response into a FetchResult, applying body-marker sniffing and redirect tracking."""
    if resp is None:
        return FetchResult(url=url, latency=latency, failure_type='connection_failed', final_url=url)
    
    status = getattr(resp, "status", 0)
    final_url = getattr(resp, "url", None) or url
    raw_body = getattr(resp, "body", None)
    if isinstance(raw_body, bytes):
        html = raw_body.decode(getattr(resp, "encoding", "utf-8") or "utf-8", errors="ignore")
    elif isinstance(raw_body, str):
        html = raw_body
    else:
        html = ""

    # Sniff HTML meta-refresh or JS window.location redirect if response is a thin redirect shell
    if html and len(html) < 2000:
        import re
        meta_match = re.search(r'<meta[^>]*http-equiv=["\']?refresh["\']?[^>]*content=["\']?\d+;\s*url=([^"\'>]+)', html, re.IGNORECASE)
        if meta_match:
            dest = meta_match.group(1).strip()
            if dest.startswith(("http://", "https://")):
                final_url = dest
        else:
            js_match = re.search(r'(?:window\.)?location(?:\.href)?\s*=\s*["\'](https?://[^"\']+)["\']', html, re.IGNORECASE)
            if js_match:
                final_url = js_match.group(1).strip()

    if status >= 400 or not html.strip():
        ft = _classify_failure(status_code=status, html=html)
        return FetchResult(url=url, status_code=status, latency=latency, failure_type=ft, final_url=final_url)

    result = FetchResult(url=url, status_code=status, html=html, latency=latency, final_url=final_url)
    body = html[:5000].lower()
    if any(m in body for m in _BLOCKED_BODY_MARKERS):
        result.failure_type = 'blocked'
    elif any(m in body for m in _PARKED_BODY_MARKERS):
        result.failure_type = 'parked'
    return result


async def fetch(url: str, domain_id: str, timeout_seconds=10, per_domain_delay=2.0, retries=2, server_error_retries=2) -> FetchResult:
    """Fetch a URL using AsyncFetcher with TLS/browser impersonation.

    A response classified as 'server_error' (5xx) is retried up to
    `server_error_retries` times with a short backoff before giving up — these are
    often transient (maintenance/overload), and treating them as permanently dead
    on the first hit would freeze a possibly-live gambling site into the 'dead'
    bucket, which is never auto-rechecked.
    """
    result = None
    for attempt in range(server_error_retries + 1):
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
            # If HTTPS failed (e.g. SSL cert issue, connection refused), try plain HTTP once
            if url.startswith("https://"):
                http_url = "http://" + url[8:]
                try:
                    resp = await AsyncFetcher.get(
                        http_url,
                        timeout=timeout_seconds,
                        impersonate=IMPERSONATE,
                        stealthy_headers=True,
                        follow_redirects=True,
                        retries=1,
                    )
                    latency = asyncio.get_running_loop().time() - start
                    return _result_from_response(http_url, resp, latency)
                except Exception:
                    pass

            latency = asyncio.get_running_loop().time() - start
            return FetchResult(url=url, latency=latency, error=str(e), failure_type='connection_failed')

        latency = asyncio.get_running_loop().time() - start
        result = _result_from_response(url, resp, latency)

        if result.failure_type != 'server_error' or attempt >= server_error_retries:
            return result

        await asyncio.sleep(per_domain_delay * (attempt + 1))

    return result

