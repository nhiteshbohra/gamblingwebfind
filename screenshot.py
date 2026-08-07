"""
screenshot.py — Stage 8: Full-page homepage screenshot capture via Playwright.
Saves PNG files to screenshots/<domain>.png
"""

import csv
import logging
from pathlib import Path
from typing import List
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import config

logging.basicConfig(
    filename=config.LOGS_DIR / "screenshot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def capture_screenshot(domain: str, output_dir: Path = config.SCREENSHOTS_DIR) -> str:
    """
    Captures full-page homepage screenshot of a domain using Playwright Chromium.
    Returns relative screenshot filepath or empty string on failure.
    """
    screenshot_filename = f"{domain}.png"
    target_path = output_dir / screenshot_filename
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=config.SCREENSHOT_VIEWPORT,
            user_agent=config.USER_AGENT,
            ignore_https_errors=True
        )
        page = context.new_page()

        loaded = False
        for scheme in ["https", "http"]:
            url = f"{scheme}://{domain}"
            try:
                page.goto(
                    url,
                    timeout=config.SCREENSHOT_TIMEOUT,
                    wait_until="domcontentloaded"
                )
                page.wait_for_timeout(2000)  # short pause for dynamic rendering
                page.screenshot(path=str(target_path), full_page=True)
                loaded = True
                break
            except PlaywrightTimeoutError:
                logging.warning(f"Timeout capturing screenshot for {url}")
            except Exception as e:
                logging.debug(f"Failed to capture {url}: {e}")

        browser.close()

        if loaded and target_path.exists():
            return f"screenshots/{screenshot_filename}"

    return ""


def run_screenshots(domains: List[str] = None):
    """
    Runs batch screenshot capture for target domains.
    """
    if domains is None:
        domains = []
        if config.SITE_INTEL_CSV.exists():
            with open(config.SITE_INTEL_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain") and row.get("is_active") == "True":
                        domains.append(row["domain"].strip())
        elif config.CLASSIFIED_CSV.exists():
            with open(config.CLASSIFIED_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain") and row.get("is_gambling") == "True":
                        domains.append(row["domain"].strip())

    domains = list(dict.fromkeys(domains))

    print(f"[*] Starting Homepage Screenshot Capture for {len(domains)} active domains...")
    logging.info(f"Starting screenshot capture for {len(domains)} domains.")

    captured = 0
    for idx, domain in enumerate(domains, start=1):
        print(f"  [{idx}/{len(domains)}] Capturing screenshot for {domain}...")
        path = capture_screenshot(domain)
        if path:
            captured += 1
            print(f"    [+] Saved: {path}")
        else:
            print(f"    [-] Failed to capture screenshot for {domain}")

    print(f"[+] Screenshot capture complete. {captured}/{len(domains)} screenshots saved to {config.SCREENSHOTS_DIR}.")
    logging.info(f"Screenshot capture finished. {captured}/{len(domains)} successful.")


if __name__ == "__main__":
    run_screenshots()
