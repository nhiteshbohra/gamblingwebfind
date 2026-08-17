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


def is_valid_screenshot(filepath: str, min_size_bytes: int = 5000) -> bool:
    """Validate that screenshot exists and is a non-empty, non-corrupted image."""
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
            if stat.stddev[0] < 2.0:  # Pure empty flat solid background
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
    clean_dom = url_or_domain.replace("https://", "").replace("http://", "").rstrip("/")
    candidates = {
        _url_to_filename(url_or_domain),
        _url_to_filename(f"https://{clean_dom}"),
        _url_to_filename(f"http://{clean_dom}"),
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
        if url.startswith("https://"):
            urls_to_try.append("http://" + url[8:])
        elif url.startswith("http://"):
            urls_to_try.append("https://" + url[7:])

        last_status = "dead"
        last_reason = "No response / Timed out after 20s (dead)"

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

                nav_timeout = 25000  # Dynamic: captures fast sites instantly, max 25s ceiling for slow sites
                page.set_default_timeout(nav_timeout)

                # Attempt page navigation (returns immediately as soon as DOM content is ready)
                try:
                    await page.goto(target_url, timeout=nav_timeout, wait_until='domcontentloaded')
                except Exception:
                    try:
                        await page.goto(target_url, timeout=5000, wait_until='commit')
                    except Exception:
                        pass

                # Quick 0.3s render settle
                try:
                    await asyncio.sleep(0.3)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                    await asyncio.sleep(0.2)
                    await page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

                # Clean any obstructive translation/cookie banners
                try:
                    await page.evaluate("""() => {
                        document.querySelectorAll('.translation-overlay, .modal-backdrop, [class*="overlay"]:not([class*="hero"]), #cookie-law-info-again').forEach(el => el.remove());
                    }""")
                except Exception:
                    pass

                # Take screenshot directly
                raw_bytes = await page.screenshot(type='png', full_page=False, timeout=15000)

                await context.close()
                context = None

                # Convert to optimized JPEG
                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=68, optimize=True)

                if is_valid_screenshot(filepath):
                    return filepath, "success", []
                else:
                    if os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
                    last_status = "dead"
                    last_reason = "Blank / invalid render (dead)"

            except Exception as e:
                err_text = str(e).lower()
                last_status = "dead"
                last_reason = "No response / Timed out after 25s (dead)"

                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                delete_screenshot(url, output_dir)

                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                await asyncio.sleep(1.0)

        delete_screenshot(url, output_dir)
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
