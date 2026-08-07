#!/usr/bin/env python3
"""
screenshotter.py — Capture homepage screenshots of live domains using Playwright.

Uses headless Chromium to render full pages including JavaScript content,
which is critical for gambling sites that are often JS-heavy SPAs.

Usage:
    python screenshotter.py --domain example.com
    python screenshotter.py --input live_domains.txt
    python screenshotter.py --input live_domains.txt --output-dir screenshots/
"""

import argparse
import os
import sys
import time

import config
import utils

logger = utils.setup_logging("screenshotter")


def capture_screenshot(
    domain: str,
    output_dir: str = config.SCREENSHOT_DIR,
    width: int = config.SCREENSHOT_WIDTH,
    height: int = config.SCREENSHOT_HEIGHT,
    timeout: int = config.SCREENSHOT_TIMEOUT,
) -> str:
    """
    Capture a screenshot of a domain's homepage using Playwright.
    Returns the file path of the saved screenshot, or empty string on failure.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "Playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        return ""

    os.makedirs(output_dir, exist_ok=True)
    filename = utils.sanitize_filename(domain) + ".png"
    filepath = os.path.join(output_dir, filename)

    # Try HTTPS first, then HTTP
    urls = [f"https://{domain}", f"http://{domain}"]

    for url in urls:
        try:
            logger.info(f"📸 Capturing screenshot: {url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-web-security",
                        "--ignore-certificate-errors",
                    ],
                )
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    ignore_https_errors=True,
                    user_agent=utils.get_random_user_agent(),
                )
                page = context.new_page()

                # Block unnecessary resources for speed
                page.route(
                    "**/*.{mp4,webm,ogg,mp3,wav,flac,aac}",
                    lambda route: route.abort(),
                )

                page.goto(url, wait_until="domcontentloaded", timeout=timeout)

                # Wait a bit for JS to render
                page.wait_for_timeout(3000)

                page.screenshot(path=filepath, full_page=False)
                browser.close()

            logger.info(f"  ✅ Saved: {filepath}")
            return filepath

        except Exception as e:
            logger.debug(f"  Screenshot failed for {url}: {e}")
            continue

    logger.warning(f"  ❌ Could not capture screenshot for {domain}")
    return ""


def capture_screenshots_batch(
    domains: list[str],
    output_dir: str = config.SCREENSHOT_DIR,
    delay: float = 2.0,
) -> dict[str, str]:
    """
    Capture screenshots for a batch of domains.
    Returns a dict of {domain: screenshot_path}.
    """
    results = {}
    total = len(domains)

    for i, domain in enumerate(domains, 1):
        logger.info(f"[{i}/{total}] Screenshotting {domain}...")
        path = capture_screenshot(domain, output_dir=output_dir)
        results[domain] = path

        if i < total:
            time.sleep(delay)

    captured = sum(1 for v in results.values() if v)
    logger.info(f"\n📸 Screenshots captured: {captured}/{total}")
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Capture homepage screenshots.")
    parser.add_argument("--domain", help="Single domain to screenshot")
    parser.add_argument("--input", "-i", help="File with one domain per line")
    parser.add_argument(
        "--output-dir",
        default=config.SCREENSHOT_DIR,
        help=f"Directory for screenshots (default: {config.SCREENSHOT_DIR})"
    )
    args = parser.parse_args()

    domains = []
    if args.domain:
        domains.append(utils.sanitize_domain(args.domain))
    if args.input:
        domains.extend(utils.sanitize_domain(d) for d in utils.read_lines_from_file(args.input))

    domains = utils.deduplicate(domains)
    if not domains:
        print("No domains to screenshot. Provide --domain or --input.", file=sys.stderr)
        sys.exit(1)

    results = capture_screenshots_batch(domains, output_dir=args.output_dir)

    print(f"\n📸 Done. Screenshots saved to: {args.output_dir}")
    for domain, path in results.items():
        status = "✅" if path else "❌"
        print(f"  {status} {domain}: {path or 'FAILED'}")


if __name__ == "__main__":
    main()
