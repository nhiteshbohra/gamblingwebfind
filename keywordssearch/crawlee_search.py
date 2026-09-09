"""
keywordssearch/crawlee_search.py — Crawlee & Native Multi-Engine Harvester (Replaces SearXNG).

Eliminates Docker Desktop, Redis, and port 8080 dependencies completely.
Supports:
  1. Multi-Engine Keyword Search: Bing, DuckDuckGo, Yahoo, Mojeek via Crawlee / Playwright / curl_cffi.
  2. Casino Hub & Review Portal Spider: Crawls gambling aggregator/affiliate sites to extract live operator links.
  3. Live-Check & Upsert: Pre-checks domain HTTP responsiveness before upserting into MongoDB `domain_Listed`.

Usage:
  python -m keywordssearch.crawlee_search --keyword "cricket betting sites"
  python -m keywordssearch.crawlee_search --keywords-file gambling_top_944_keywords.json --limit 20
  python -m keywordssearch.crawlee_search --hub-url "https://example-casino-review.com"
"""
import argparse
import asyncio
import functools
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force unbuffered output for log monitoring
print = functools.partial(print, flush=True)

import aiohttp
import tldextract
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

# Load root .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

# ── Configuration ─────────────────────────────────────────────────────────────

CRAWLEE_CONCURRENCY    = int(os.getenv("CRAWLEE_CONCURRENCY", "10"))
CRAWLEE_HEADLESS       = os.getenv("CRAWLEE_HEADLESS", "true").lower() == "true"
PAGE_DELAY_SECONDS     = float(os.getenv("PAGE_DELAY_SECONDS", "1.5"))
REQUEST_TIMEOUT        = float(os.getenv("REQUEST_TIMEOUT", "15.0"))
EMPTY_PAGE_TOLERANCE   = int(os.getenv("EMPTY_PAGE_TOLERANCE", "3"))
SAVE_EVERY_N_DOMAINS   = int(os.getenv("SAVE_EVERY_N_DOMAINS", "30"))

LIVE_CHECK_CONCURRENCY = int(os.getenv("LIVE_CHECK_CONCURRENCY", "50"))
LIVE_CHECK_TIMEOUT     = float(os.getenv("LIVE_CHECK_TIMEOUT", "8.0"))

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Blocklist (Search Engines, Social Media, CDNs, Meta-Sites) ───────────────

_BLOCKLIST = {
    "google.com", "google.co.in", "google.com.hk", "bing.com",
    "duckduckgo.com", "yahoo.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "reddit.com", "wikipedia.org",
    "linkedin.com", "amazon.com", "apple.com", "microsoft.com", "t.co",
    "tiktok.com", "pinterest.com", "tumblr.com", "quora.com",
    "medium.com", "wordpress.com", "blogspot.com", "github.com",
    "cloudflare.com", "t.me", "wa.me", "whatsapp.com",
    "searx.be", "searxng.org", "searx.info", "search.brave.com",
    "startpage.com", "ecosia.org", "yandex.com", "yandex.ru",
    "baidu.com", "ask.com", "aol.com", "mojeek.com",
}


def extract_domain(url: str) -> str | None:
    """Return clean registered domain from a URL, or None if invalid or blocklisted."""
    try:
        ext = tldextract.extract(url)
        domain = (ext.registered_domain or ext.domain).lower().strip()
        if not domain or "." not in domain:
            return None
        if domain in _BLOCKLIST:
            return None
        return domain
    except Exception:
        return None


# ── MongoDB Helpers ───────────────────────────────────────────────────────────

def get_mongo_collection():
    """Return the source domain collection from MongoDB."""
    from db.mongo_client import source_domains, get_db, _client
    try:
        db = get_db()
        db.command("ping")
        return source_domains(), _client
    except Exception as e:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        raise ConnectionError(
            f"[crawlee_search] Cannot connect to MongoDB at '{uri}': {e}\n"
            "Make sure MongoDB is running."
        )


async def _live_check_domain(domain: str, session: aiohttp.ClientSession, sem: asyncio.Semaphore) -> bool:
    """Check if domain is responsive (HEAD/GET https/http)."""
    timeout = aiohttp.ClientTimeout(total=LIVE_CHECK_TIMEOUT)
    attempts = [
        ("HEAD", f"https://{domain}"),
        ("GET",  f"https://{domain}"),
        ("HEAD", f"http://{domain}"),
        ("GET",  f"http://{domain}"),
    ]
    async with sem:
        for method, url in attempts:
            try:
                async with session.request(
                    method, url,
                    allow_redirects=True,
                    timeout=timeout,
                    ssl=False,
                    headers=DEFAULT_HEADERS,
                ) as r:
                    return True
            except Exception:
                continue
    return False


async def save_domains_to_mongo_with_livecheck(
    domains: list[str], collection, session: aiohttp.ClientSession, batch_size: int = 500
) -> tuple[int, int, int]:
    """Live-check new domains and upsert into MongoDB with active=True/False."""
    if not domains:
        return 0, 0, 0

    clean_unique = list(dict.fromkeys(d for d in domains if d and d not in _BLOCKLIST))
    existing = set()
    for i in range(0, len(clean_unique), batch_size):
        chunk = clean_unique[i: i + batch_size]
        try:
            docs = collection.find({"_id": {"$in": chunk}}, {"_id": 1})
            existing.update(d["_id"] for d in docs)
        except Exception as e:
            print(f"  [live-check] MongoDB query error: {e}")

    already_in_db = len(existing)
    new_domains = [d for d in clean_unique if d not in existing]

    if not new_domains:
        return 0, 0, already_in_db

    sem = asyncio.Semaphore(LIVE_CHECK_CONCURRENCY)
    results = await asyncio.gather(*[_live_check_domain(d, session, sem) for d in new_domains])

    active_domains   = [d for d, ok in zip(new_domains, results) if ok]
    inactive_domains = [d for d, ok in zip(new_domains, results) if not ok]

    today = datetime.now(IST).strftime("%Y-%m-%d")
    ops = [
        UpdateOne(
            {"_id": d},
            {"$setOnInsert": {
                "_id": d,
                "domain": d,
                "active": is_active,
                "processed": False,
                "added_date": today,
                "source": "crawlee_search",
            }},
            upsert=True,
        )
        for d, is_active in zip(new_domains, results)
    ]

    inserted = 0
    for i in range(0, len(ops), batch_size):
        try:
            r = collection.bulk_write(ops[i: i + batch_size], ordered=False)
            inserted += r.upserted_count
        except Exception as e:
            print(f"  [live-check] MongoDB bulk write error: {e}")

    print(
        f"  [live-check] Saved {len(new_domains)} domains "
        f"(active={len(active_domains)}, inactive={len(inactive_domains)}) "
        f"| already_in_db={already_in_db}"
    )
    return len(active_domains), len(inactive_domains), already_in_db


# ── Engine 1: DuckDuckGo Native Async Harvester ──────────────────────────────

async def search_duckduckgo(keyword: str, max_results: int = 50) -> list[str]:
    """Search DuckDuckGo using duckduckgo_search or curl_cffi/BeautifulSoup fallback."""
    urls = []
    # 1. Try duckduckgo_search library if available
    try:
        from duckduckgo_search import DDGS
        results = list(DDGS().text(keyword, max_results=max_results))
        for r in results:
            href = r.get("href") or r.get("link") or ""
            if href.startswith(("http://", "https://")):
                urls.append(href)
        if urls:
            return urls
    except Exception:
        pass

    # 2. Fast HTML scraping with curl_cffi or aiohttp fallback
    try:
        from curl_cffi import requests as cffi_requests
        resp = await asyncio.to_thread(
            cffi_requests.get,
            "https://html.duckduckgo.com/html/",
            params={"q": keyword, "kl": "in-en"},
            headers=DEFAULT_HEADERS,
            impersonate="chrome",
            timeout=10,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a.result__a"):
                href = a.get("href", "")
                if "duckduckgo.com/l/" in href:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]
                    href = urllib.parse.unquote(href)
                if href.startswith(("http://", "https://")):
                    urls.append(href)
    except Exception as e:
        print(f"  [DDG] scraping fallback error: {e}")

    return urls


# ── Engine 2: Bing Playwright / Crawlee Harvester ────────────────────────────

def _decode_bing_url(href: str) -> str:
    """Bing wraps results in /ck/a? redirect. Extract base64 u parameter."""
    if "/ck/a?" not in href and "bing.com/ck/a" not in href:
        return href
    try:
        parsed = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(parsed.query)
        u_val = params.get("u", [""])[0]
        if u_val.startswith("a1"):
            u_val = u_val[2:]
        missing_padding = len(u_val) % 4
        if missing_padding:
            u_val += "=" * (4 - missing_padding)
        import base64
        decoded = base64.b64decode(u_val).decode("utf-8", errors="ignore")
        if decoded.startswith(("http://", "https://")):
            return decoded
    except Exception:
        pass
    return href


async def search_bing_playwright(keyword: str, max_pages: int = 4) -> list[str]:
    """Search Bing using Playwright browser context, bypassing CAPTCHAs."""
    from playwright.async_api import async_playwright
    urls = []
    seen = set()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=CRAWLEE_HEADLESS)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=DEFAULT_HEADERS["User-Agent"],
            )
            page = await context.new_page()

            encoded_kw = urllib.parse.quote_plus(keyword)
            for page_num in range(1, max_pages + 1):
                first_param = (page_num - 1) * 10 + 1
                search_url = f"https://www.bing.com/search?q={encoded_kw}&first={first_param}&FORM=PORE"

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(1000)

                    elements = await page.query_selector_all("li.b_algo h2 a, li.b_algo a[href]")
                    page_new = 0
                    for el in elements:
                        href = await el.get_attribute("href")
                        if not href:
                            continue
                        final_url = _decode_bing_url(href)
                        if final_url.startswith(("http://", "https://")) and final_url not in seen:
                            seen.add(final_url)
                            urls.append(final_url)
                            page_new += 1

                    if page_new == 0:
                        break
                except Exception as page_err:
                    break

            await browser.close()
    except Exception as e:
        print(f"  [Bing-Playwright] Error: {e}")

    return urls


# ── Engine 3: Casino Hub / Aggregator Spider (Crawlee-powered) ───────────────

async def crawl_casino_hub_spider(hub_urls: list[str], max_depth: int = 1) -> list[str]:
    """
    Crawlee Casino Hub Spider:
    Takes casino review & aggregator websites (e.g. 'Top 50 Betting Sites India'),
    follows outbound links, and extracts operator landing domains.
    """
    discovered_urls = []
    seen_urls = set()

    # Check if crawlee is available
    has_crawlee = False
    try:
        from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
        has_crawlee = True
    except ImportError:
        pass

    if has_crawlee:
        try:
            print(f"[crawlee] Launching Crawlee BeautifulSoupCrawler on {len(hub_urls)} hub URLs...")
            crawler = BeautifulSoupCrawler(
                max_requests_per_crawl=100,
                concurrency_settings={"max_concurrency": CRAWLEE_CONCURRENCY},
            )

            @crawler.router.default_handler
            async def request_handler(context: BeautifulSoupCrawlingContext) -> None:
                current_url = context.request.url
                soup = context.soup
                if not soup:
                    return

                # Extract all anchor links
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    full_url = urllib.parse.urljoin(current_url, href)
                    if full_url.startswith(("http://", "https://")):
                        dom = extract_domain(full_url)
                        hub_dom = extract_domain(current_url)
                        # Keep links going to OTHER domains (the actual gambling operators/affiliates)
                        if dom and dom != hub_dom and full_url not in seen_urls:
                            seen_urls.add(full_url)
                            discovered_urls.append(full_url)

            await crawler.run(hub_urls)
            print(f"[crawlee] Hub crawl complete. Discovered {len(discovered_urls)} external links.")
            return discovered_urls
        except Exception as e:
            print(f"[crawlee] Hub crawler encountered error: {e}. Falling back to native async spider.")

    # Fallback to native aiohttp hub spider if crawlee is not installed
    async with aiohttp.ClientSession(headers=DEFAULT_HEADERS) as session:
        for hub_url in hub_urls:
            try:
                print(f"  [hub-spider] Spidering: {hub_url}")
                async with session.get(hub_url, timeout=aiohttp.ClientTimeout(total=12), ssl=False) as resp:
                    if resp.status == 200:
                        html = await resp.text(errors="ignore")
                        soup = BeautifulSoup(html, "html.parser")
                        hub_dom = extract_domain(hub_url)
                        for a in soup.find_all("a", href=True):
                            href = a["href"].strip()
                            full_url = urllib.parse.urljoin(hub_url, href)
                            dom = extract_domain(full_url)
                            if dom and dom != hub_dom and full_url not in seen_urls:
                                seen_urls.add(full_url)
                                discovered_urls.append(full_url)
            except Exception as e:
                print(f"  [hub-spider] Failed to scrape {hub_url}: {e}")

    return discovered_urls


# ── Unified Pipeline Runner ──────────────────────────────────────────────────

async def run_keyword_harvest(
    keywords: list[str],
    hub_urls: list[str] = None,
    save_batch: int = SAVE_EVERY_N_DOMAINS,
) -> int:
    """Run full keyword harvesting & hub spidering without SearXNG or Docker."""
    coll, client = get_mongo_collection()
    print(f"\n[+] Connected to MongoDB. Target collection: '{coll.name}'")
    print(f"[+] Total keywords queued: {len(keywords)}")
    if hub_urls:
        print(f"[+] Total casino hub portals queued: {len(hub_urls)}")

    total_discovered_domains = 0
    buffer_domains: list[str] = []

    async with aiohttp.ClientSession() as session:
        # Phase 1: Hub Spidering (if any review hubs provided)
        if hub_urls:
            print("\n" + "═" * 70)
            print("  PHASE 1: CASINO AGGREGATOR / REVIEW HUB SPIDER")
            print("═" * 70)
            hub_raw_urls = await crawl_casino_hub_spider(hub_urls)
            for raw_u in hub_raw_urls:
                dom = extract_domain(raw_u)
                if dom:
                    buffer_domains.append(dom)

            if buffer_domains:
                new_act, new_inact, in_db = await save_domains_to_mongo_with_livecheck(
                    buffer_domains, coll, session
                )
                total_discovered_domains += (new_act + new_inact)
                buffer_domains.clear()

        # Phase 2: Multi-Engine Search for Keywords
        print("\n" + "═" * 70)
        print("  PHASE 2: MULTI-ENGINE KEYWORD HARVESTING (No Docker / No SearXNG)")
        print("═" * 70)

        for kw_idx, kw in enumerate(keywords, 1):
            print(f"\n[{kw_idx}/{len(keywords)}] Harvesting keyword: '{kw}'")

            # 1. DuckDuckGo
            ddg_urls = await search_duckduckgo(kw)
            print(f"  -> DuckDuckGo: {len(ddg_urls)} URLs")

            # 2. Bing Playwright
            bing_urls = await search_bing_playwright(kw, max_pages=3)
            print(f"  -> Bing Playwright: {len(bing_urls)} URLs")

            combined_urls = ddg_urls + bing_urls
            for u in combined_urls:
                dom = extract_domain(u)
                if dom:
                    buffer_domains.append(dom)

            # Flush periodically to avoid data loss
            if len(buffer_domains) >= save_batch:
                new_act, new_inact, in_db = await save_domains_to_mongo_with_livecheck(
                    buffer_domains, coll, session
                )
                total_discovered_domains += (new_act + new_inact)
                buffer_domains.clear()

            await asyncio.sleep(PAGE_DELAY_SECONDS)

        # Final flush
        if buffer_domains:
            new_act, new_inact, in_db = await save_domains_to_mongo_with_livecheck(
                buffer_domains, coll, session
            )
            total_discovered_domains += (new_act + new_inact)
            buffer_domains.clear()

    print(f"\n[✓] Harvest finished! Total new domains added to DB: {total_discovered_domains}")
    return total_discovered_domains


# ── CLI Interface ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Crawlee & Native Multi-Engine Domain Harvester (SearXNG Replacement)"
    )
    parser.add_argument("--keyword", "-k", type=str, help="Single keyword to search")
    parser.add_argument("--keywords-file", "-f", type=str, default="gambling_top_944_keywords.json",
                        help="JSON file containing list of keywords")
    parser.add_argument("--hub-url", type=str, help="Single casino review / aggregator portal to spider")
    parser.add_argument("--hub-file", type=str, help="File with casino review/hub URLs (one per line)")
    parser.add_argument("--limit", "-l", type=int, default=10, help="Max keywords to process from file")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")

    args = parser.parse_args()

    keywords = []
    if args.keyword:
        keywords = [args.keyword.strip()]
    elif args.keywords_file:
        kw_path = Path(args.keywords_file)
        if not kw_path.is_absolute():
            kw_path = _PROJECT_ROOT / args.keywords_file
        if kw_path.exists():
            with open(kw_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    keywords = data[:args.limit]
                elif isinstance(data, dict):
                    keywords = list(data.keys())[:args.limit]

    hub_urls = []
    if args.hub_url:
        hub_urls.append(args.hub_url.strip())
    if args.hub_file:
        hf = Path(args.hub_file)
        if not hf.is_absolute():
            hf = _PROJECT_ROOT / args.hub_file
        if hf.exists():
            with open(hf, "r", encoding="utf-8") as f:
                hub_urls.extend([line.strip() for line in f if line.strip() and not line.startswith("#")])

    if not keywords and not hub_urls:
        print("[!] No keywords or hub URLs provided. Use --keyword or --keywords-file.")
        sys.exit(1)

    asyncio.run(run_keyword_harvest(keywords=keywords, hub_urls=hub_urls))


if __name__ == "__main__":
    main()
