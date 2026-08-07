"""
directory_crawler.py — Scrapes online gambling directory sites & unmasks affiliate redirect links.
Extracts candidate URLs and resolves final destination domains.
"""

import csv
import logging
import time
from typing import List, Dict, Set
from urllib.parse import urljoin, urlparse
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import config
from domain_utils import normalize_domain, is_valid_domain

logging.basicConfig(
    filename=config.LOGS_DIR / "directory_crawler.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

TARGET_DIRECTORY_URLS = [
    "https://www.casinofreak.com/reviews/all-online-casinos",
    "https://www.top10casinos.com/casino-list/index.html",
    "https://listofallbookmakers.com/bookmaker-licenses-list",
    "https://www.top100bookmakers.com/completelist.php",
    "https://bestonlinebookmakers.com/top-bookmakers-rating.html"
]

IGNORED_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "linkedin.com", "pinterest.com", "google.com", "googletagmanager.com",
    "cloudflare.com", "t.me", "telegram.org", "apple.com", "play.google.com",
    "wordpress.org", "w3.org"
}


def is_valid_outbound_link(base_url: str, target_url: str) -> bool:
    if not target_url or not target_url.startswith("http"):
        return False
    base_domain = urlparse(base_url).netloc.replace("www.", "")
    target_domain = urlparse(target_url).netloc.replace("www.", "")
    
    if not target_domain or target_domain == base_domain:
        return False
    for ignored in IGNORED_DOMAINS:
        if ignored in target_domain:
            return False
    return True


def resolve_final_destination(redirect_url: str) -> str:
    """
    Follows affiliate redirects (e.g. site.com/visit/casino) to uncover real target domain.
    """
    try:
        resp = requests.head(redirect_url, headers=config.HTTP_HEADERS, allow_redirects=True, timeout=8)
        return resp.url
    except requests.RequestException:
        try:
            resp = requests.get(redirect_url, headers=config.HTTP_HEADERS, allow_redirects=True, timeout=8, stream=True)
            return resp.url
        except requests.RequestException:
            return redirect_url


def scrape_directory_page(page_url: str, max_scrolls: int = 4) -> List[str]:
    """
    Renders dynamic directory page using Playwright Chromium and collects outbound links.
    """
    found_urls = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=config.USER_AGENT)
        page = context.new_page()

        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=35000)
            for _ in range(max_scrolls):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1000)

            raw_hrefs = page.eval_on_selector_all("a[href]", "elements => elements.map(e => e.href)")
            for href in raw_hrefs:
                full_url = urljoin(page_url, href.strip())
                if is_valid_outbound_link(page_url, full_url):
                    found_urls.add(full_url)
        except PlaywrightTimeoutError:
            logging.warning(f"Timeout loading directory page {page_url}")
        except Exception as e:
            logging.error(f"Error scraping directory page {page_url}: {e}")
        finally:
            browser.close()

    return list(found_urls)


def run_directory_crawler(directory_urls: List[str] = None) -> Set[str]:
    """
    Runs directory scraper across target aggregator sites and extracts normalized root domains.
    """
    if directory_urls is None:
        directory_urls = TARGET_DIRECTORY_URLS

    print(f"[*] Starting Directory Scraping on {len(directory_urls)} aggregator sites...")
    logging.info(f"Starting directory scraping across {len(directory_urls)} sites.")

    discovered_domains = set()

    for idx, dir_url in enumerate(directory_urls, start=1):
        print(f"  [{idx}/{len(directory_urls)}] Scraping Directory: {dir_url}")
        outbound_links = scrape_directory_page(dir_url)
        print(f"    Found {len(outbound_links)} candidate links. Resolving redirects...")

        for link in outbound_links[:40]:  # limit to top 40 outbound links per directory to keep pacing steady
            final_url = resolve_final_destination(link)
            domain = normalize_domain(final_url)
            if domain and is_valid_domain(domain):
                discovered_domains.add(domain)

        time.sleep(1)

    print(f"[+] Directory Crawler complete. Extracted {len(discovered_domains)} unique target domains.")
    logging.info(f"Directory Crawler complete. Found {len(discovered_domains)} domains.")

    return discovered_domains


if __name__ == "__main__":
    run_directory_crawler()
