import os
import io
import asyncio
import urllib.parse
from PIL import Image
from playwright.async_api import async_playwright

def _url_to_filename(url: str) -> str:
    """Generate a clean, filesystem-safe filename from a URL."""
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc or parsed.path
    clean_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in netloc)
    return f"{clean_name}.jpg"

async def capture_async(url: str, output_dir: str, viewport_width: int = 1280, viewport_height: int = 720, quality: int = 68, timeout_seconds: int = 12) -> str | None:
    """Capture a screenshot of a single URL using Playwright and save to output_dir.
    Returns saved image file path on success, or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    filename = _url_to_filename(url)
    filepath = os.path.join(output_dir, filename)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={'width': viewport_width, 'height': viewport_height},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                ignore_https_errors=True
            )
            page = await context.new_page()
            try:
                try:
                    await page.goto(url, timeout=timeout_seconds * 1000, wait_until='domcontentloaded')
                except Exception:
                    await page.goto(url, timeout=timeout_seconds * 1000, wait_until='commit')
                await asyncio.sleep(0.5)

                raw_bytes = await page.screenshot(type='png', full_page=False)
                await context.close()
                await browser.close()

                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=quality, optimize=True)
                return filepath
            except Exception as e:
                await context.close()
                await browser.close()
                print(f"[Screenshot] Failed to capture {url}: {e}")
                return None
    except Exception as e:
        print(f"[Screenshot] Playwright browser error for {url}: {e}")
        return None

def capture(url: str, output_dir: str) -> str | None:
    """Synchronous wrapper for capture_async."""
    return asyncio.run(capture_async(url, output_dir))
