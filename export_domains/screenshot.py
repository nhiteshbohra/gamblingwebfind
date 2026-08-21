import os
import io
import asyncio
import urllib.parse
from PIL import Image, ImageStat
from playwright.async_api import async_playwright, Browser, Playwright
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


def all_filename_candidates(url: str, domain: str) -> list[str]:
    """Every filename a screenshot for this domain could have been saved under."""
    clean_dom = domain.removeprefix("www.")
    return list(dict.fromkeys([
        _url_to_filename(url),
        _url_to_filename(f"https://{clean_dom}"),
        _url_to_filename(f"http://{clean_dom}"),
        _url_to_filename(f"https://www.{clean_dom}"),
        _url_to_filename(f"http://www.{clean_dom}"),
    ]))


def is_valid_screenshot(filepath: str, min_size_bytes: int = 4000) -> bool:
    """
    Validate that screenshot exists, is readable, has substantial content (>4KB),
    and is NOT a blank/solid color screen or a loading spinner splash screen.
    """
    if not filepath or not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) < min_size_bytes:
        return False
    try:
        with Image.open(filepath) as img:
            img.verify()
        with Image.open(filepath) as img:
            rgb_img = img.convert('RGB')
            # 1. Standard deviation of luminance (must have real visual variation, not flat solid/splash screen)
            stat = ImageStat.Stat(rgb_img.convert('L'))
            if stat.stddev[0] < 12.0:
                return False

            # 2. Check unique color diversity across quantized thumbnail
            thumb = rgb_img.resize((128, 90)).quantize(colors=32)
            num_unique_colors = len(thumb.getcolors(maxcolors=64) or [])
            if num_unique_colors < 6:
                return False

        return True
    except Exception:
        return False


def delete_screenshot(url_or_domain: str, output_dir: str = None) -> bool:
    """Delete screenshot file(s) for a given domain/URL if found on disk."""
    if not url_or_domain:
        return False
    if output_dir is None:
        output_dir = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))

    if not os.path.exists(output_dir):
        return False

    deleted = False
    clean_dom = url_or_domain.replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/")
    candidates = set(all_filename_candidates(url_or_domain, clean_dom)) | {
        _url_to_filename(clean_dom),
    }

    for cand in candidates:
        cand_path = os.path.join(output_dir, cand)
        if os.path.exists(cand_path):
            try:
                os.remove(cand_path)
                deleted = True
            except Exception:
                pass
    return deleted


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
                try:
                    if self.browser:
                        await self.browser.close()
                except Exception:
                    pass
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

    async def capture_url(self, url: str, output_dir: str, retries: int = 2, keywords: set = None) -> tuple[str | None, str, list | str]:
        """
        Direct capture function:
          - If the website responds / opens: take screenshot immediately.
          - If the website cannot be reached / times out after 15-20s / connection refused / DNS failed: mark as dead.
        """
        os.makedirs(output_dir, exist_ok=True)
        filename = _url_to_filename(url)
        filepath = os.path.join(output_dir, filename)

        # Re-use an existing valid screenshot if already on disk
        if is_valid_screenshot(filepath):
            return filepath, "success", []

        urls_to_try = [url]
        clean_dom = url.replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/")
        if not url.startswith("https://www."):
            urls_to_try.append(f"https://www.{clean_dom}")
        urls_to_try.append(f"http://{clean_dom}")
        urls_to_try.append(f"http://www.{clean_dom}")

        last_status = "timeout"
        last_reason = "No response after multiple protocol attempts"

        for attempt in range(max(retries, len(urls_to_try))):
            target_url = urls_to_try[attempt % len(urls_to_try)]
            context = None
            try:
                await self.ensure_browser()
                try:
                    context = await self.browser.new_context(
                        viewport={'width': 1280, 'height': 900},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        ignore_https_errors=True
                    )
                except Exception:
                    self.browser = None
                    await self.ensure_browser()
                    context = await self.browser.new_context(
                        viewport={'width': 1280, 'height': 900},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        ignore_https_errors=True
                    )
                page = await context.new_page()

                nav_timeout = 25000  # Dynamic: max 25s ceiling
                page.set_default_timeout(nav_timeout)

                # 1. Attempt navigation with full 'load' event (assets, scripts, styles)
                response = None
                try:
                    response = await page.goto(target_url, timeout=nav_timeout, wait_until='load')
                except Exception:
                    try:
                        response = await page.goto(target_url, timeout=nav_timeout, wait_until='domcontentloaded')
                    except Exception:
                        pass

                # 2. Check HTTP status code immediately if available
                if response:
                    http_status = response.status
                    if http_status in (500, 502, 503, 504):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "dead", f"Dead: HTTP {http_status} Server Error in Playwright"
                    elif http_status == 403:
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "blocked", "Blocked: HTTP 403 Forbidden in Playwright"
                    elif http_status in (404, 410):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "dead", f"Dead: HTTP {http_status} Not Found in Playwright"

                # 3. Wait for network to settle (React/Vue API requests & betting widgets to finish)
                try:
                    await page.wait_for_load_state('networkidle', timeout=6000)
                except Exception:
                    pass

                # 4. Wait for real DOM content to render (text and interactive elements)
                try:
                    await page.wait_for_function("""() => {
                        const body = document.body;
                        if (!body) return false;
                        const text = body.innerText ? body.innerText.trim() : '';
                        const elements = document.querySelectorAll('img, canvas, button, a, svg, iframe, video, table, [class*="game"], [class*="bet"], [class*="card"], [class*="slot"], [class*="odds"]');
                        return (text.length > 80 && elements.length >= 6) || elements.length >= 14;
                    }""", timeout=7000)
                except Exception:
                    pass

                # 5. Dismiss / remove full-screen loading spinners, splash screens & click 18+ modals
                try:
                    await page.evaluate("""() => {
                        // Remove full-screen loaders & splash screens
                        document.querySelectorAll('.loading, .loader, .spinner, .preloader, #loading, #spinner, [class*="preloader"], [class*="loading-overlay"], [class*="loading-wrap"], .splash, #splash, .app-loader').forEach(el => el.remove());
                        
                        // Click common age-gate / cookie / welcome modal buttons
                        const buttons = document.querySelectorAll('button, a.btn, input[type="button"], [class*="btn"]');
                        for (const btn of buttons) {
                            const txt = (btn.innerText || '').toLowerCase().trim();
                            if (txt.includes('18+') || txt.includes('i am 18') || txt.includes('agree') || txt.includes('accept all') || txt.includes('continue') || txt.includes('enter site')) {
                                try { btn.click(); } catch(e) {}
                            }
                        }
                    }""")
                except Exception:
                    pass

                # 6. Progressive scroll to trigger lazy-loaded game banners/images
                try:
                    await page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight / 3, 600))")
                    await asyncio.sleep(1.0)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

                # 7. Clean obstructive translation/cookie banners
                try:
                    await page.evaluate("""() => {
                        document.querySelectorAll('.translation-overlay, .modal-backdrop, [class*="overlay"]:not([class*="hero"]), #cookie-law-info-again, .cookies-popup, [class*="cookie-banner"]').forEach(el => el.remove());
                    }""")
                except Exception:
                    pass

                # 8. Check rendered DOM text for 403 / Cloudflare WAF or Domain Parking landers
                try:
                    rendered_title = (await page.title() or "").lower()
                    rendered_body = (await page.content() or "")[:15000].lower()
                    rendered_full = f"{rendered_title} {rendered_body}"

                    _BLOCKED_PAGE_MARKERS = [
                        "403 forbidden", "access denied", "just a moment...", "cf-challenge",
                        "checking your browser", "enable javascript and cookies",
                        "attention required! | cloudflare", "access to this page is denied",
                        "cloudflare ray id", "error 403", "403 error", "403 - forbidden",
                    ]
                    _PARKED_MARKERS = [
                        "is for sale", "domain for sale", "this domain is for sale",
                        "this domain is available for sale", "buy this domain", "purchase this domain",
                        "make an offer on this domain", "parked domain", "parked by", "parked free",
                        "sedo.com", "sedoparking", "dan.com", "afternic.com", "hugedomains.com",
                        "atom.com", "squadhelp.com", "godaddy.com/domains", "parkingcrew",
                    ]

                    _SERVER_ERROR_MARKERS = [
                        "502 bad gateway", "503 service unavailable", "504 gateway time-out",
                        "504 gateway timeout", "502 server error", "503 service temporarily unavailable",
                        "bad gateway</title>", "502 error", "error 502", "error 504",
                    ]

                    _NOT_FOUND_MARKERS = [
                        "404 not found", "http error 404", "404 - not found", "error 404",
                        "the requested resource is not found", "page not found", "404 page not found",
                        "404 error", "<title>404</title>", "not found</title>",
                    ]

                    if any(m in rendered_full for m in _BLOCKED_PAGE_MARKERS):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "blocked", "Blocked by Cloudflare WAF / 403 Forbidden in Playwright"

                    if any(m in rendered_full for m in _PARKED_MARKERS):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "dead", "Dead: Parked or For-Sale lander detected in Playwright"

                    if any(m in rendered_full for m in _SERVER_ERROR_MARKERS):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "dead", "Dead: 502/503/504 Server Error detected in Playwright"

                    if any(m in rendered_full for m in _NOT_FOUND_MARKERS):
                        await context.close()
                        context = None
                        delete_screenshot(clean_dom, os.path.dirname(filepath))
                        return None, "dead", "Dead: 404 Not Found error page detected in Playwright"
                except Exception:
                    pass

                # 9. Take screenshot with in-flight retry if still unmounted
                raw_bytes = await page.screenshot(type='png', full_page=False, timeout=15000)

                # If blank, loading spinner, or solid background, allow extra 3s for JS bundle/WebSockets to mount
                img_test = Image.open(io.BytesIO(raw_bytes)).convert('RGB')
                stat_test = ImageStat.Stat(img_test.convert('L'))
                thumb_test = img_test.resize((128, 90)).quantize(colors=32)
                num_colors = len(thumb_test.getcolors(maxcolors=64) or [])

                if stat_test.stddev[0] < 12.0 or num_colors < 6:
                    try:
                        await asyncio.sleep(3.0)
                        await page.evaluate("window.scrollTo(0, 350)")
                        await asyncio.sleep(0.8)
                        await page.evaluate("window.scrollTo(0, 0)")
                        await asyncio.sleep(0.5)
                        raw_bytes = await page.screenshot(type='png', full_page=False, timeout=15000)
                        img_test = Image.open(io.BytesIO(raw_bytes)).convert('RGB')
                        stat_test = ImageStat.Stat(img_test.convert('L'))
                        thumb_test = img_test.resize((128, 90)).quantize(colors=32)
                        num_colors = len(thumb_test.getcolors(maxcolors=64) or [])
                    except Exception:
                        pass

                await context.close()
                context = None

                # If still blank white/solid color/loader after retry, reject as unrenderable/dead
                if stat_test.stddev[0] < 12.0 or num_colors < 6:
                    delete_screenshot(clean_dom, os.path.dirname(filepath))
                    return None, "dead", "Dead: Blank/Solid color/Loader page rendered in Playwright"

                # Convert to optimized JPEG
                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=75, optimize=True)

                if is_valid_screenshot(filepath):
                    return filepath, "success", []

            except Exception as e:
                err_text = str(e).lower()
                if "name_not_resolved" in err_text or "getaddrinfo" in err_text:
                    last_status = "dns_failed"
                    last_reason = "DNS Error: Domain name not resolved (dead)"
                elif "connection_refused" in err_text:
                    last_status = "connection_refused"
                    last_reason = "Connection Refused by server (dead)"
                elif "connection_reset" in err_text or "ssl" in err_text or "cert" in err_text:
                    last_status = "ssl_or_reset"
                    last_reason = "SSL / Connection Reset"
                elif "403" in err_text or "challenge" in err_text or "cloudflare" in err_text:
                    last_status = "blocked"
                    last_reason = "Blocked by Cloudflare WAF / Anti-Bot"
                elif "timeout" in err_text or "timed out" in err_text:
                    last_status = "timeout"
                    last_reason = "Network Timeout: Server took >25s without response (dead)"
                else:
                    last_status = "error"
                    last_reason = f"Capture failed: {str(e)[:60]}"

                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                await asyncio.sleep(0.5)

        return None, last_status, last_reason


async def capture_async(url: str, output_dir: str) -> str | None:
    pool = BrowserPool(concurrency=1)
    await pool.start()
    try:
        fp, _, _ = await pool.capture_url(url, output_dir, retries=2)
        return fp
    finally:
        await pool.close()

def capture(url: str, output_dir: str) -> str | None:
    return asyncio.run(capture_async(url, output_dir))
