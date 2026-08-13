import os
import io
import asyncio
import urllib.parse
from PIL import Image
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

def is_valid_screenshot(filepath: str, min_size_bytes: int = 4000) -> bool:
    """Validate that screenshot exists, is a valid JPEG, > min_size_bytes, and not a blank single-color image."""
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
            if extrema[0] == extrema[1]:  # Completely solid color (blank white/black)
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
        if not self.browser or not self.browser.is_connected():
            await self.start()

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

    async def capture_url(self, url: str, output_dir: str, retries: int = 3) -> tuple[str | None, str, str]:
        os.makedirs(output_dir, exist_ok=True)
        filename = _url_to_filename(url)
        filepath = os.path.join(output_dir, filename)

        # Check existing valid screenshot
        if is_valid_screenshot(filepath):
            return filepath, "success", ""

        urls_to_try = [url]
        if url.startswith("https://"):
            urls_to_try.append("http://" + url[8:])
        elif url.startswith("http://"):
            urls_to_try.append("https://" + url[7:])

        last_failure_type = "dead"
        last_reason = "timeout or blank"

        for attempt in range(retries):
            await self.ensure_browser()
            target_url = urls_to_try[attempt % len(urls_to_try)]
            context = None
            try:
                context = await self.browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    ignore_https_errors=True
                )
                page = await context.new_page()
                page.set_default_timeout(15000)

                wait_strategy = 'domcontentloaded' if attempt == 0 else ('load' if attempt == 1 else 'commit')
                response = None
                try:
                    response = await page.goto(target_url, timeout=12000, wait_until=wait_strategy)
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

                if response:
                    status_code = response.status
                    if status_code in (403, 429):
                        last_failure_type = "blocked"
                        last_reason = f"HTTP {status_code} Blocked"
                    elif status_code == 404:
                        last_failure_type = "dead"
                        last_reason = "HTTP 404 Not Found"
                    elif status_code >= 500:
                        last_failure_type = "dead"
                        last_reason = f"HTTP {status_code} Server Error"

                # Check page body content for WAF or Parked markers
                try:
                    content = (await page.content()).lower()
                    if any(m in content for m in ("checking your browser", "cf-challenge", "captcha", "just a moment", "cf_chl_opt")):
                        last_failure_type = "blocked"
                        last_reason = "Cloudflare / WAF Blocked"
                    elif any(m in content for m in ("this domain is for sale", "domain for sale", "buy this domain", "parked by", "sedo.com")):
                        last_failure_type = "dead"
                        last_reason = "Domain Parked / For Sale"
                except Exception:
                    pass

                await asyncio.sleep(0.5 if attempt == 0 else 1.5)

                raw_bytes = await page.screenshot(type='png', full_page=False)
                await context.close()
                context = None

                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=68, optimize=True)

                if is_valid_screenshot(filepath):
                    return filepath, "success", ""
                else:
                    if os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
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
                await asyncio.sleep(0.5 * (attempt + 1))

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

