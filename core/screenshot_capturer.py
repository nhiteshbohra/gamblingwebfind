import io
import asyncio
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

class ScreenshotCapturer:
    def __init__(self, viewport_width=1280, viewport_height=720, quality=68, timeout_seconds=10):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.quality = quality
        self.timeout_seconds = timeout_seconds

    async def capture_urls_batch(self, items: list[tuple[int, str]], concurrency: int = 5, on_progress=None):
        """Capture screenshots for a list of (index, url) tuples using a single shared Playwright browser instance.
        Fast, lightweight, and thread-safe.
        """
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            sem = asyncio.Semaphore(concurrency)

            async def _capture(idx: int, url: str):
                async with sem:
                    try:
                        context = await browser.new_context(
                            viewport={'width': self.viewport_width, 'height': self.viewport_height},
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                        )
                        page = await context.new_page()
                        try:
                            await page.goto(url, timeout=self.timeout_seconds * 1000, wait_until='load')
                            await asyncio.sleep(0.3)
                            raw_bytes = await page.screenshot(type='png', full_page=False)
                            await context.close()

                            img = Image.open(io.BytesIO(raw_bytes))
                            if img.mode != 'RGB':
                                img = img.convert('RGB')

                            buf = io.BytesIO()
                            img.save(buf, format='JPEG', quality=self.quality, optimize=True)
                            buf.seek(0)
                            return idx, url, buf
                        except Exception:
                            await context.close()
                            return idx, url, self._generate_placeholder(url)
                    except Exception:
                        return idx, url, self._generate_placeholder(url)

            tasks = [_capture(idx, url) for idx, url in items]
            for f in asyncio.as_completed(tasks):
                idx, url, buf = await f
                results.append((idx, url, buf))
                if on_progress:
                    on_progress()

            await browser.close()

        # Sort strictly by target index to maintain report order
        results.sort(key=lambda r: r[0])
        return results

    def _generate_placeholder(self, url: str) -> io.BytesIO:
        """Generate a clean gray placeholder image (1280x720) for failed / unreachable captures."""
        img = Image.new('RGB', (1280, 720), color=(235, 238, 242))
        draw = ImageDraw.Draw(img)
        text = f"Screenshot Unavailable / Blocked\n{url}"
        draw.text((640, 360), text, fill=(120, 130, 140), anchor="mm")

        output_buffer = io.BytesIO()
        img.save(output_buffer, format='JPEG', quality=self.quality, optimize=True)
        output_buffer.seek(0)
        return output_buffer
