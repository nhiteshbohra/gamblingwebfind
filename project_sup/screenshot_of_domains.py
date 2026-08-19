#!/usr/bin/env python3
"""
screenshot_domains.py

Reads a list of domains/URLs, opens each in a headless browser,
waits until the page is fully loaded, and saves a screenshot.

Requirements:
    pip install playwright
    playwright install chromium

Usage:
    python screenshot_domains.py
    python screenshot_domains.py --input active.txt --output-dir screenshots
    python screenshot_domains.py --full-page --width 1920 --height 1080
"""

import argparse
import asyncio
import hashlib
import os
import re
import sys
import urllib.parse
from datetime import datetime
from urllib.parse import urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ---- Edit this list directly, or pass domains via --input file ----
DEFAULT_DOMAINS = [
    "https://www.example.com",
    "https://www.wikipedia.org",
    "https://www.python.org",
]


def normalize_url(domain: str) -> str:
    """Ensure the domain has a scheme (defaults to https)."""
    domain = domain.strip()
    if not domain:
        return domain
    if not re.match(r"^https?://", domain, re.IGNORECASE):
        domain = "https://" + domain
    return domain


def safe_filename(url: str) -> str:
    """Generate a clean, filesystem-safe, unique filename from a URL.

    Combines the domain + a snippet of the path with a short md5 hash of
    the full URL, so different URLs never collide even if their cleaned-up
    domain/path look similar (e.g. example.com/a?x=1 vs example.com/a?x=2).
    """
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc or parsed.path
    path = parsed.path

    clean_domain = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in netloc)
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    clean_path = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in path.strip("/"))[:25]

    if clean_path:
        return f"{clean_domain}_{clean_path}_{url_hash}.jpg"
    return f"{clean_domain}_{url_hash}.jpg"


def load_domains(input_path: str | None) -> list[str]:
    if input_path:
        with open(input_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        return lines
    return DEFAULT_DOMAINS


async def screenshot_one(context, url: str, output_dir: str, full_page: bool,
                          timeout_ms: int, extra_wait_ms: int) -> dict:
    """
    Open a single URL, wait for it to be fully loaded, and take a screenshot.
    'Fully loaded' here means: DOM content loaded + network idle (no
    outstanding requests for 500ms) + an optional extra settle time for
    JS-heavy pages (animations, lazy-loaded images, etc).
    """
    url = normalize_url(url)
    page = await context.new_page()
    result = {"url": url, "status": "ok", "error": None, "file": None}

    try:
        # Navigate and wait for the network to go idle (a good proxy for
        # "page has finished loading", including most async JS/XHR calls).
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Give extra time for any late animations / lazy content to settle.
        if extra_wait_ms > 0:
            await page.wait_for_timeout(extra_wait_ms)

        filename = safe_filename(url)
        filepath = os.path.join(output_dir, filename)
        await page.screenshot(path=filepath, full_page=full_page)

        result["file"] = filepath
        print(f"[OK]   {url} -> {filepath}")

    except PlaywrightTimeoutError:
        # Fall back: try to screenshot whatever loaded so far instead of
        # failing outright (useful for pages with never-ending background
        # requests, e.g. analytics beacons, websockets, etc).
        try:
            filename = safe_filename(url)
            filepath = os.path.join(output_dir, filename)
            await page.screenshot(path=filepath, full_page=full_page)
            result["status"] = "timeout_but_captured"
            result["file"] = filepath
            print(f"[WARN] {url} timed out waiting for network idle; captured anyway -> {filepath}")
        except Exception as e2:
            result["status"] = "error"
            result["error"] = str(e2)
            print(f"[FAIL] {url} -> {e2}")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"[FAIL] {url} -> {e}")

    finally:
        await page.close()

    return result


async def run(domains: list[str], output_dir: str, full_page: bool,
               width: int, height: int, timeout_ms: int, extra_wait_ms: int,
               concurrency: int, warning_log: str | None = "warnings.txt"):
    os.makedirs(output_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": width, "height": height})

        semaphore = asyncio.Semaphore(concurrency)

        async def bound_task(url):
            async with semaphore:
                return await screenshot_one(context, url, output_dir, full_page, timeout_ms, extra_wait_ms)

        results = await asyncio.gather(*(bound_task(d) for d in domains))

        await context.close()
        await browser.close()

    # Summary
    ok = sum(1 for r in results if r["status"] in ("ok", "timeout_but_captured"))
    warnings = [r for r in results if r["status"] == "timeout_but_captured"]
    print(f"\nDone: {ok}/{len(results)} screenshots captured into '{output_dir}'")

    if warning_log and warnings:
        with open(warning_log, "w", encoding="utf-8") as f:
            for w in warnings:
                f.write(w["url"] + "\n")
        print(f"Logged {len(warnings)} warning site(s) to '{warning_log}'")

    return results


def parse_args():
    parser = argparse.ArgumentParser(description="Screenshot a list of domains once fully loaded.")
    parser.add_argument("--input", "-i", help="Path to a text file with one domain/URL per line.")
    parser.add_argument("--output-dir", "-o", default="screenshots", help="Directory to save screenshots into.")
    parser.add_argument("--warning-log", "-w", default="warnings.txt", help="Path to write warning sites to (default: warnings.txt).")
    parser.add_argument("--full-page", action="store_true", help="Capture the full scrollable page, not just the viewport.")
    parser.add_argument("--width", type=int, default=1440, help="Viewport width.")
    parser.add_argument("--height", type=int, default=900, help="Viewport height.")
    parser.add_argument("--timeout", type=int, default=30000, help="Max time (ms) to wait for a page to finish loading.")
    parser.add_argument("--extra-wait", type=int, default=500, help="Extra settle time (ms) after network idle, for JS-heavy pages.")
    parser.add_argument("--concurrency", type=int, default=3, help="How many pages to process in parallel.")
    return parser.parse_args()


def main():
    args = parse_args()
    domains = load_domains(args.input)

    if not domains:
        print("No domains to process. Add some to DEFAULT_DOMAINS, or pass --input a_file.txt")
        sys.exit(1)

    print(f"Processing {len(domains)} domain(s)...\n")
    asyncio.run(run(
        domains=domains,
        output_dir=args.output_dir,
        full_page=args.full_page,
        width=args.width,
        height=args.height,
        timeout_ms=args.timeout,
        extra_wait_ms=args.extra_wait,
        concurrency=args.concurrency,
        warning_log=args.warning_log,
    ))


if __name__ == "__main__":
    main()