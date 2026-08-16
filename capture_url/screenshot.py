import os
import io
import asyncio
import urllib.parse
from PIL import Image, ImageStat
from playwright.async_api import async_playwright, Browser, Playwright

from checking_url.classifier import classify, load_keywords

import hashlib

def _url_to_filename(url: str) -> str:
    """Generate a clean, filesystem-safe unique filename from a URL."""
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc or parsed.path
    path = parsed.path
    clean_domain = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in netloc)
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
    clean_path = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in path.strip('/'))[:25]
    if clean_path:
        return f"{clean_domain}_{clean_path}_{url_hash}.jpg"
    return f"{clean_domain}_{url_hash}.jpg"

def is_valid_screenshot(filepath: str, min_size_bytes: int = 6000) -> bool:
    """
    Validate that screenshot:
    1. Exists and is >= 6KB.
    2. Is a valid uncorrupted image.
    3. Is NOT a blank solid color or near-blank white/black screen (stat.stddev < 3.0).
    """
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) < min_size_bytes:
        return False
    try:
        with Image.open(filepath) as img:
            img.verify()
        with Image.open(filepath) as img:
            gray = img.convert('L')
            extrema = gray.getextrema()
            if extrema[0] == extrema[1]:
                return False
            stat = ImageStat.Stat(gray)
            if stat.stddev[0] < 3.0:  # Pure white, pure dark, or empty flat background
                return False
        return True
    except Exception:
        return False


class BrowserPool:
    def __init__(self, concurrency: int = 15):
        self.concurrency = concurrency
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if not self.playwright:
                self.playwright = await async_playwright().start()
            if not self.browser or not self.browser.is_connected():
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-gpu',
                        '--disable-blink-features=AutomationControlled'
                    ]
                )

    async def ensure_browser(self):
        async with self._lock:
            if not self.playwright:
                self.playwright = await async_playwright().start()
            if not self.browser or not self.browser.is_connected():
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-gpu',
                        '--disable-blink-features=AutomationControlled'
                    ]
                )

    async def close(self):
        async with self._lock:
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass
                self.browser = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None

    async def capture_url(self, url: str, output_dir: str, retries: int = 3, keywords: set = None) -> tuple[str | None, str, list | str]:
        """
        Double-layer capture function:
          Layer 1: Real-time WAF / 403 Forbidden / Parked lander check.
          Layer 2: Real-time live HTML keyword re-verification (classify >= 3 keywords).
          If verified: takes screenshot and returns (filepath, "success", matched_reasons).
          If failed: returns (None, "regular" | "blocked" | "dead", reason).
        """
        os.makedirs(output_dir, exist_ok=True)
        filename = _url_to_filename(url)
        filepath = os.path.join(output_dir, filename)

        # Re-use an existing valid screenshot if it's already on disk
        if is_valid_screenshot(filepath):
            return filepath, "success", []

        urls_to_try = [url]
        if url.startswith("https://"):
            urls_to_try.append("http://" + url[8:])
        elif url.startswith("http://"):
            urls_to_try.append("https://" + url[7:])

        last_failure_type = "dead"
        last_reason = "timeout or blank"
        matched_reasons = []  # guard: always defined even if raw_html is empty on success path

        for attempt in range(retries):
            await self.ensure_browser()
            target_url = urls_to_try[attempt % len(urls_to_try)]
            context = None
            try:
                context = await self.browser.new_context(
                    viewport={'width': 1280, 'height': 900},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    ignore_https_errors=True
                )
                page = await context.new_page()

                # ── Navigation ──────────────────────────────────────────────
                nav_strategies = ['domcontentloaded', 'load', 'commit']
                wait_strategy = nav_strategies[min(attempt, len(nav_strategies) - 1)]
                nav_timeout = 25000 + attempt * 10000

                page.set_default_timeout(nav_timeout)
                response = None
                try:
                    response = await page.goto(target_url, timeout=nav_timeout, wait_until=wait_strategy)
                except Exception as nav_err:
                    err_str = str(nav_err).lower()
                    last_reason = f"Navigation error: {str(nav_err)[:80]}"
                    if "403" in err_str or "access denied" in err_str:
                        last_failure_type = "blocked"
                    else:
                        last_failure_type = "dead"
                    try:
                        response = await page.goto(target_url, timeout=12000, wait_until='commit')
                    except Exception:
                        pass

                # ── LAYER 1: Response & WAF / 403 / Parked Status Check ───────
                is_blocked_page = False
                is_dead_page = False

                if response:
                    status_code = response.status
                    if status_code in (403, 429):
                        is_blocked_page = True
                        last_failure_type = "blocked"
                        last_reason = f"HTTP {status_code} Blocked"
                    elif status_code == 404:
                        is_dead_page = True
                        last_failure_type = "dead"
                        last_reason = "HTTP 404 Not Found"
                    elif status_code >= 500:
                        is_dead_page = True
                        last_failure_type = "dead"
                        last_reason = f"HTTP {status_code} Server Error"

                raw_html = ""
                try:
                    raw_html = await page.content()
                    content_lower = raw_html.lower()
                    title_lower = (await page.title()).lower()
                    full_text = title_lower + " " + content_lower

                    waf_markers = (
                        "checking your browser", "cf-challenge", "captcha", "just a moment",
                        "cf_chl_opt", "403 forbidden", "error 403", "you don't have permission to access",
                        "access denied", "403 - forbidden", "403: forbidden", "forbidden - 403"
                    )
                    parked_markers = (
                        "this domain is for sale", "domain for sale", "buy this domain",
                        "parked by", "sedo.com", "hugedomains.com", "dan.com"
                    )

                    if any(m in full_text for m in waf_markers):
                        is_blocked_page = True
                        last_failure_type = "blocked"
                        last_reason = "Cloudflare / WAF / 403 Forbidden Page"
                    elif any(m in full_text for m in parked_markers):
                        is_dead_page = True
                        last_failure_type = "dead"
                        last_reason = "Domain Parked / For Sale"
                except Exception:
                    pass

                # If blocked or dead page detected, abort immediately
                if is_blocked_page or is_dead_page:
                    await context.close()
                    context = None
                    if os.path.exists(filepath):
                        try:
                            # User requested NOT to delete the image, keep it in folder
                            pass # os.remove(filepath)
                        except Exception:
                            pass
                    return None, last_failure_type, last_reason

                # ── LAYER 2: Live HTML Parked / WAF Re-check ─────────────────────────
                if raw_html:
                    decision, matched_reasons = classify(
                        raw_html, url=target_url, keywords=keywords
                    )
                    # Only reject if page changed to a parked lander or negative archetype during navigation
                    if any("excluded:" in str(r) for r in matched_reasons):
                        await context.close()
                        context = None
                        return None, "regular", matched_reasons
                else:
                    matched_reasons = []

                # ── Wait for full page render ────────────────────────────────
                try:
                    await page.wait_for_load_state('networkidle', timeout=8000)
                except Exception:
                    pass

                # ── Trigger lazy-loaded content ──────────────────────────────
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.5)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

                # ── Settle delay ─────────────────────────────────────────────
                settle_delay = 1.0 + attempt * 0.5   # 1.0s → 1.5s → 2.0s
                await asyncio.sleep(settle_delay)

                # ── Clean modal & dark translation overlays ──────────────────
                try:
                    await page.evaluate("""() => {
                        document.querySelectorAll('.translation-overlay, .modal-backdrop, [class*="overlay"]:not([class*="hero"]), #cookie-law-info-again').forEach(el => el.remove());
                    }""")
                except Exception:
                    pass

                # ── Take screenshot ──────────────────────────────────────────
                raw_bytes = await page.screenshot(type='png', full_page=False, timeout=25000)

                # Check if image is suspiciously small (< 45 KB PNG) or mostly dark spinner screen
                if len(raw_bytes) < 45000:
                    # Give Single Page Apps / React / Angular 5 extra seconds to finish rendering
                    await asyncio.sleep(5.0)
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(0.5)
                        await page.evaluate("window.scrollTo(0, 0)")
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
                    raw_bytes = await page.screenshot(type='png', full_page=False)

                await context.close()
                context = None

                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=68, optimize=True)

                if is_valid_screenshot(filepath):
                    return filepath, "success", matched_reasons
                else:
                    if os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
                    last_failure_type = "dead"
                    last_reason = "Blank / solid color render discarded"

            except Exception as e:
                err_text = str(e)
                if "403" in err_text:
                    last_failure_type = "blocked"
                    last_reason = "HTTP 403 Forbidden"
                else:
                    last_failure_type = "dead"
                    last_reason = f"Failed: {err_text[:80]}"

                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                await asyncio.sleep(1.0 * (attempt + 1))  # 1s → 2s → 3s back-off

        return None, last_failure_type, last_reason

# Fallback one-off capture function for backward compatibility
async def capture_async(url: str, output_dir: str, viewport_width: int = 1280, viewport_height: int = 720, quality: int = 68, timeout_seconds: int = 12) -> str | None:
    pool = BrowserPool(concurrency=1)
    await pool.start()
    try:
        fp, _, _ = await pool.capture_url(url, output_dir, retries=2)
        return fp
    finally:
        await pool.close()

def capture(url: str, output_dir: str) -> str | None:
    return asyncio.run(capture_async(url, output_dir))
