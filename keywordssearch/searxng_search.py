"""
keywordssearch/searxng_search.py — Unlimited multi-engine keyword crawler.

Engines (tried in order, all contribute URLs):
  1. DuckDuckGo  — direct HTML scraping, no API key, many pages
  2. Playwright/Chromium — real browser, scrapes Google like a human, ALL pages

Flow per keyword:
  • DuckDuckGo pagination runs until no more results (unlimited pages)
  • Playwright/Google runs until no "Next" button found (unlimited pages)
  • All URLs from both engines are combined, deduplicated, domains extracted
  • Domains saved to MongoDB incrementally every SAVE_EVERY_N_DOMAINS

MongoDB: domain_Listed collection
  { _id: domain, domain, active: True, processed: False, added_date }

SearXNG Docker is still supported as an optional 3rd engine if it's running.
Config from root .env via python-dotenv.
"""

import asyncio
import functools
import os
import re
import subprocess
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

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

SEARXNG_BASE_URL   = os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
PAGE_DELAY_SECONDS = float(os.getenv("SEARXNG_PAGE_DELAY", "2.0"))   # polite delay between pages
REQUEST_TIMEOUT    = float(os.getenv("SEARXNG_TIMEOUT",    "20.0"))   # per-request timeout

# How many consecutive zero-result pages before declaring a keyword exhausted on ONE engine.
EMPTY_PAGE_TOLERANCE = int(os.getenv("SEARXNG_EMPTY_TOLERANCE", "3"))

# Auto-save to MongoDB every N new domains (protects against Ctrl+C data loss)
SAVE_EVERY_N_DOMAINS = int(os.getenv("SEARXNG_SAVE_EVERY", "50"))

# Use DuckDuckGo direct scraping (recommended, no API key)
USE_DUCKDUCKGO = os.getenv("USE_DUCKDUCKGO", "true").lower() == "true"

# Use Playwright/Chromium to scrape Google (more results, slower)
USE_PLAYWRIGHT = os.getenv("USE_PLAYWRIGHT", "true").lower() == "true"

# Use SearXNG Docker if it's available
USE_SEARXNG = os.getenv("USE_SEARXNG", "true").lower() == "true"

IST = timezone(timedelta(hours=5, minutes=30))

# Path to docker-compose.yml
_COMPOSE_DIR  = Path(__file__).resolve().parent
_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.yml"

# ── Domain blocklist ──────────────────────────────────────────────────────────

_BLOCKLIST = {
    "google.com", "google.co.in", "google.com.hk", "bing.com",
    "duckduckgo.com", "yahoo.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", "reddit.com", "wikipedia.org",
    "linkedin.com", "amazon.com", "apple.com", "microsoft.com", "t.co",
    "tiktok.com", "pinterest.com", "tumblr.com", "quora.com",
    "medium.com", "wordpress.com", "blogspot.com",
    # Meta-search / SearXNG self-referential noise
    "searx.be", "searxng.org", "searx.info", "search.brave.com",
    "startpage.com", "ecosia.org", "yandex.com", "yandex.ru",
    "baidu.com", "ask.com", "aol.com",
}


def extract_domain(url: str) -> str | None:
    """Return registered domain from a URL, or None if invalid/blocklisted."""
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


# ── MongoDB helpers ───────────────────────────────────────────────────────────

def get_mongo_collection():
    """Return the domain_Listed collection from MongoDB."""
    uri      = os.getenv("MONGO_URI",        "mongodb://localhost:27017/")
    db_name  = os.getenv("MONGO_DB_NAME",    "gamblingsites")
    coll_name = os.getenv("MONGO_COLLECTION", "domain_Listed")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return client[db_name][coll_name], client
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        raise ConnectionError(
            f"[searxng_search] Cannot connect to MongoDB at '{uri}': {e}\n"
            "Make sure MongoDB is running."
        )


def save_domains_to_mongo(domains: list[str], collection, batch_size: int = 500) -> int:
    """
    Upsert domains into domain_Listed.
    New domains get: active=True, processed=False, added_date=today.
    Existing domains are NOT overwritten (setOnInsert only).
    Returns count of new domains inserted.
    """
    if not domains:
        return 0

    today = datetime.now(IST).strftime("%Y-%m-%d")
    operations = [
        UpdateOne(
            {"_id": domain},
            {"$setOnInsert": {
                "_id": domain,
                "domain": domain,
                "active": True,
                "processed": False,
                "added_date": today,
            }},
            upsert=True,
        )
        for domain in domains
    ]

    total = 0
    for i in range(0, len(operations), batch_size):
        try:
            result = collection.bulk_write(operations[i: i + batch_size], ordered=False)
            total += result.upserted_count
        except Exception as e:
            print(f"[searxng_search] MongoDB bulk write error: {e}")
    return total


# ── Default HTTP headers ──────────────────────────────────────────────────────

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}


# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE 1 — DuckDuckGo (curl_cffi — real Chrome TLS fingerprint, no bot detection)
#  curl_cffi impersonates Chrome at the TLS layer, so DDG cannot detect it as a bot.
#  Falls back to aiohttp if curl_cffi is not installed.
# ══════════════════════════════════════════════════════════════════════════════

def _ddg_get_sync(params: dict, impersonate: str = "chrome124") -> tuple[int, str]:
    """
    GET request to DDG HTML search using curl_cffi (Chrome TLS impersonation).
    GET requests bypass DDG's 202 POST challenge. Returns (status_code, html_text).
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(params)
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(
            url,
            impersonate=impersonate,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=30,
        )
        return resp.status_code, resp.text
    except ImportError:
        import requests as req
        resp = req.get(
            url,
            headers={
                **DEFAULT_HEADERS,
                "Referer": "https://html.duckduckgo.com/",
            },
            timeout=30,
        )
        return resp.status_code, resp.text


async def duckduckgo_crawl_keyword(
    keyword: str,
    session: aiohttp.ClientSession,          # kept for API compat
    delay: float = PAGE_DELAY_SECONDS,
    timeout: float = REQUEST_TIMEOUT,
) -> list[str]:
    """
    Scrape DuckDuckGo HTML search for a keyword -- ALL available pages.

    Uses GET requests with curl_cffi (Chrome TLS impersonation) → 100% HTTP 200.
    Runs multiple query variations to surface different result sets.

    Stops only when:
      - No more results (EMPTY_PAGE_TOLERANCE consecutive empty responses)
      - User presses Ctrl+C
    """
    print(f"\n  [DDG] Starting DuckDuckGo crawl for: '{keyword}'")

    all_urls: list[str] = []
    seen: set[str] = set()

    # DDG query variations — same strategy as Bing to surface different results
    query_variations = [
        keyword,
        f"{keyword} site:.com",
        f"{keyword} site:.in",
        f"{keyword} online",
        f'"{keyword}"',
    ]

    for variation_idx, query in enumerate(query_variations):
        print(f"  [DDG] '{keyword}' | variation {variation_idx+1}/{len(query_variations)}: query='{query}'")
        page_num = 0
        consecutive_empty = 0
        stagnant_pages = 0
        get_params = {"q": query, "kl": "in-en"}

        while True:
            page_num += 1
            page_urls: list[str] = []
            next_params: dict | None = None

            try:
                # Run sync curl_cffi GET in thread pool (non-blocking)
                status, html = await asyncio.to_thread(_ddg_get_sync, get_params)

                if status in (200, 302):
                    soup = BeautifulSoup(html, "html.parser")

                    # Extract result URLs
                    for a in soup.select("a.result__a"):
                        href = a.get("href", "")
                        if not href:
                            continue
                        if "duckduckgo.com/l/" in href:
                            try:
                                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                                href = parsed.get("uddg", [href])[0]
                                href = urllib.parse.unquote(href)
                            except Exception:
                                pass
                        if href.startswith(("http://", "https://")) and href not in seen:
                            seen.add(href)
                            page_urls.append(href)

                    # Find "next page" form parameters
                    parent_form = soup.select_one("div.nav-link form")
                    if parent_form:
                        next_params = {
                            inp.get("name"): inp.get("value", "")
                            for inp in parent_form.select("input")
                            if inp.get("name")
                        }

                    print(
                        f"  [DDG] '{keyword}' | v{variation_idx+1} p{page_num}"
                        f" -> {len(page_urls)} new URLs | total: {len(all_urls) + len(page_urls)}"
                    )

                elif status in (429, 202):
                    print(f"  [DDG] '{keyword}' | v{variation_idx+1} p{page_num} | rate-limit ({status}) -- moving to next variation")
                    break
                else:
                    print(f"  [DDG] '{keyword}' | v{variation_idx+1} p{page_num} | HTTP {status} -- counting as empty")

            except asyncio.TimeoutError:
                print(f"  [DDG] '{keyword}' | v{variation_idx+1} p{page_num} | timeout")
            except Exception as e:
                print(f"  [DDG] '{keyword}' | v{variation_idx+1} p{page_num} | error: {type(e).__name__}")

            # Stagnation / exhaustion detection
            if page_urls:
                consecutive_empty = 0
                stagnant_pages = 0
                all_urls.extend(page_urls)
                if next_params:
                    get_params = next_params
                else:
                    print(f"  [DDG] '{keyword}' | v{variation_idx+1} | no next page -- next variation")
                    break
            else:
                consecutive_empty += 1
                if consecutive_empty >= EMPTY_PAGE_TOLERANCE:
                    print(f"  [DDG] '{keyword}' | v{variation_idx+1} exhausted -- next variation")
                    break
                if not next_params:
                    break

            await asyncio.sleep(delay)

    print(f"  [DDG] '{keyword}' DONE -- {len(all_urls)} URLs")
    return all_urls



# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE 2 — Playwright/Chromium BING scraping (real browser, ALL pages)
#  NOTE: Google blocks headless browsers with CAPTCHA. Bing works perfectly.
#        Bing India results cover virtually all Indian gambling domains.
# ══════════════════════════════════════════════════════════════════════════════

def _decode_bing_url(href: str) -> str:
    """
    Bing wraps result links in a redirect: https://www.bing.com/ck/a?...&u=a1aHR0cH...
    The real URL is base64-encoded in the 'u' param (with 'a1' prefix to strip).
    """
    import base64
    try:
        if "bing.com/ck/a" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            u_val = parsed.get("u", [""])[0]
            if u_val.startswith("a1"):
                u_val = u_val[2:]  # strip 'a1' prefix
            # Add padding if needed
            padding = 4 - len(u_val) % 4
            if padding != 4:
                u_val += "=" * padding
            decoded = base64.urlsafe_b64decode(u_val).decode("utf-8", errors="replace")
            if decoded.startswith(("http://", "https://")):
                return decoded
    except Exception:
        pass
    return href


def _extract_urls_from_bing_html(html: str, seen: set) -> list[str]:
    """Extract and decode real result URLs from Bing SERP HTML."""
    soup = BeautifulSoup(html, "html.parser")
    page_urls = []
    # Bing puts results in <li class="b_algo"> with <h2><a href="...">
    for a in soup.select("li.b_algo h2 a[href], li.b_algo .b_title a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        # Decode Bing redirect
        real_url = _decode_bing_url(href)
        if real_url.startswith(("http://", "https://")) and real_url not in seen:
            seen.add(real_url)
            page_urls.append(real_url)
    # Also grab any direct external links in result snippets
    for a in soup.select("li.b_algo a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        real_url = _decode_bing_url(href)
        if (real_url.startswith(("http://", "https://"))
                and "bing.com" not in real_url
                and "microsoft.com" not in real_url
                and "msn.com" not in real_url
                and real_url not in seen):
            seen.add(real_url)
            page_urls.append(real_url)
    return page_urls



def playwright_crawl_keyword_sync(keyword: str) -> list[str]:
    """
    Open a real Chromium browser and scrape Bing search results -- ALL pages.

    Uses Bing instead of Google because Google serves CAPTCHA to headless browsers.
    Bing works reliably with headless Chromium for unlimited pagination.

    Runs synchronously (called via asyncio.to_thread).
    Keeps clicking Bing's 'Next' button until no more pages exist.
    Only stops when Bing exhausts results or user presses Ctrl+C.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("  [PW] playwright not installed -- skipping Bing engine")
        return []

    print(f"\n  [PW/Bing] Starting Playwright/Bing crawl for: '{keyword}'")
    all_urls: list[str] = []
    seen: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        # Query variations to try — Bing recycles results for the same query,
        # so we use several phrasings to surface different URLs.
        query_variations = [
            keyword,                             # original: "casino india"
            f'"{keyword}"',                      # exact phrase: "casino india"
            f"{keyword} site:.com",              # force .com domains
            f"{keyword} site:.in",               # force Indian domains
            f"{keyword} online",                 # add "online"
            f"{keyword} -site:google.com -site:youtube.com",  # exclude noise
        ]

        try:
            for variation_idx, query in enumerate(query_variations):
                print(
                    f"  [PW/Bing] '{keyword}' | variation {variation_idx+1}/{len(query_variations)}: "
                    f"query='{query}'"
                )
                page_num = 0
                consecutive_empty = 0    # pages with raw_count == 0
                stagnant_pages = 0       # pages with raw results but 0 new unique URLs
                first_offset = 1

                while True:
                    page_num += 1
                    bing_page_url = (
                        f"https://www.bing.com/search"
                        f"?q={urllib.parse.quote(query)}"
                        f"&count=10"
                        f"&first={first_offset}"
                    )
                    try:
                        page.goto(bing_page_url, timeout=30000, wait_until="load")
                        time.sleep(2.5)
                    except Exception as e:
                        print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} p{page_num} | nav error: {type(e).__name__}")
                        time.sleep(3)

                    try:
                        html = page.content()
                    except Exception as e:
                        print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} p{page_num} | content error -- skipping")
                        first_offset += 10
                        consecutive_empty += 1
                        if consecutive_empty >= EMPTY_PAGE_TOLERANCE:
                            break
                        continue

                    from bs4 import BeautifulSoup as _BS
                    raw_result_count = len(_BS(html, "html.parser").select("li.b_algo"))
                    page_urls = _extract_urls_from_bing_html(html, seen)

                    print(
                        f"  [PW/Bing] '{keyword}' | v{variation_idx+1} p{page_num} (first={first_offset})"
                        f" -> raw={raw_result_count}, new={len(page_urls)}"
                        f" | total: {len(all_urls) + len(page_urls)}"
                    )

                    all_urls.extend(page_urls)
                    first_offset += 10

                    if raw_result_count == 0:
                        # Bing has no more results for this query
                        consecutive_empty += 1
                        print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} | no results ({consecutive_empty}/{EMPTY_PAGE_TOLERANCE})")
                        if consecutive_empty >= EMPTY_PAGE_TOLERANCE:
                            print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} exhausted -- next variation")
                            break
                    elif len(page_urls) == 0:
                        # Bing returned results but ALL are already seen = recycling
                        stagnant_pages += 1
                        consecutive_empty = 0
                        print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} | recycling ({stagnant_pages}/3)")
                        if stagnant_pages >= 3:
                            print(f"  [PW/Bing] '{keyword}' | v{variation_idx+1} recycling detected -- next variation")
                            break
                    else:
                        consecutive_empty = 0
                        stagnant_pages = 0

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  [PW/Bing] '{keyword}' | fatal error: {type(e).__name__}: {e}")
        finally:
            browser.close()

    print(f"  [PW/Bing] '{keyword}' DONE -- {len(all_urls)} URLs")
    return all_urls


# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE 3 — SearXNG (optional, Docker-based, used if running)
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_docker_in_path():
    import shutil
    if shutil.which("docker"):
        return
    possible_bins = [
        str(Path.home() / "AppData" / "Local" / "Programs" / "DockerDesktop" / "resources" / "bin"),
        r"C:\Program Files\Docker\Docker\resources\bin",
    ]
    for pb in possible_bins:
        if Path(pb).exists() and pb not in os.environ["PATH"]:
            os.environ["PATH"] = pb + os.pathsep + os.environ["PATH"]
            break


def _is_docker_daemon_running() -> bool:
    _ensure_docker_in_path()
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def start_searxng_docker() -> bool:
    """Start SearXNG Docker containers. Returns True if SearXNG becomes reachable."""
    import socket

    try:
        socket.create_connection(("127.0.0.1", 8080), timeout=2).close()
        print("[docker] SearXNG already running.")
        return True
    except OSError:
        pass

    if not _is_docker_daemon_running():
        print("[docker] Docker daemon not running — skipping SearXNG engine")
        return False

    if not _COMPOSE_FILE.exists():
        print(f"[docker] docker-compose.yml not found at {_COMPOSE_FILE}")
        return False

    print("[docker] Starting SearXNG containers...")
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(_COMPOSE_DIR), capture_output=True, text=True,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["docker-compose", "up", "-d"],
                cwd=str(_COMPOSE_DIR), capture_output=True, text=True,
            )
        if result.returncode != 0:
            print(f"[docker] docker compose failed: {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        print("[docker] 'docker' not found — skipping SearXNG engine")
        return False

    for i in range(30):
        time.sleep(1)
        try:
            socket.create_connection(("127.0.0.1", 8080), timeout=1).close()
            print(f"[docker] SearXNG ready! ({i+1}s)")
            return True
        except OSError:
            pass

    print("[docker] SearXNG not ready after 30s — skipping SearXNG engine")
    return False


async def searxng_crawl_keyword(
    keyword: str,
    session: aiohttp.ClientSession,
    delay: float = PAGE_DELAY_SECONDS,
    timeout: float = REQUEST_TIMEOUT,
) -> list[str]:
    """Crawl SearXNG unlimited pages (used only if SearXNG Docker is running)."""
    print(f"\n  [SearXNG] Starting crawl for: '{keyword}'")
    all_urls: list[str] = []
    search_url = f"{SEARXNG_BASE_URL.rstrip('/')}/search"
    base_params = {"q": keyword, "language": "all", "safesearch": "0"}
    page = 0
    consecutive_empty = 0

    while True:
        page += 1
        page_urls: list[str] = []

        # JSON API
        try:
            async with session.get(
                search_url,
                params={**base_params, "format": "json", "pageno": page},
                headers=DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for r in data.get("results", []):
                        if r.get("url"):
                            page_urls.append(r["url"])
                    print(
                        f"  [SearXNG] '{keyword}' | page {page} | {len(page_urls)} results | total: {len(all_urls)+len(page_urls)}")
                elif resp.status == 429:
                    await asyncio.sleep(10); continue
                else:
                    print(f"  [SearXNG] '{keyword}' | page {page} | JSON HTTP {resp.status}")
        except Exception as e:
            print(f"  [SearXNG] '{keyword}' | page {page} | error: {e}")

        # HTML fallback
        if not page_urls:
            try:
                async with session.get(
                    search_url,
                    params={**base_params, "pageno": page},
                    headers=DEFAULT_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        soup = BeautifulSoup(await resp.text(), "html.parser")
                        for a in soup.select("article.result h3 a, a.url_header, div.result a[href], h3.result_header a"):
                            href = a.get("href", "")
                            if href.startswith(("http://", "https://")):
                                page_urls.append(href)
                        print(f"  [SearXNG] '{keyword}' | page {page} | HTML {len(page_urls)} links | total: {len(all_urls)+len(page_urls)}")
                    elif resp.status == 429:
                        await asyncio.sleep(10); continue
            except Exception:
                pass

        if not page_urls:
            consecutive_empty += 1
            if consecutive_empty >= EMPTY_PAGE_TOLERANCE:
                print(f"  [SearXNG] '{keyword}' exhausted after {page} pages")
                break
        else:
            consecutive_empty = 0
            all_urls.extend(page_urls)

        await asyncio.sleep(delay)

    return all_urls


# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE 4 — Yahoo Search (direct HTML scraping via curl_cffi GET)
# ══════════════════════════════════════════════════════════════════════════════

def _yahoo_get_sync(url: str, impersonate: str = "chrome124") -> tuple[int, str]:
    """GET request to Yahoo Search using curl_cffi."""
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(
            url,
            impersonate=impersonate,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=30,
        )
        return resp.status_code, resp.text
    except ImportError:
        import urllib.request
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)


async def yahoo_crawl_keyword(keyword: str, max_pages_per_var: int = 150) -> list[str]:
    """
    Scrape Yahoo Search for a single keyword across multiple query variations.
    Paginates using b=1, b=11, b=21, ... up to 100+ pages per variation.
    Returns list of target URLs.
    """
    import urllib.parse
    print(f"\n  [Yahoo] Starting Yahoo Search crawl for: '{keyword}'")

    variations = [
        keyword,
        f"{keyword} site:.com",
        f"{keyword} site:.in",
        f"{keyword} site:.net",
        f"{keyword} site:.org",
        f"{keyword} online",
        f"{keyword} real money",
        f"{keyword} login",
    ]

    all_urls: list[str] = []
    seen_urls: set[str] = set()

    for v_idx, query in enumerate(variations, 1):
        page = 1
        stagnant_pages = 0
        print(f"  [Yahoo] '{keyword}' | variation {v_idx}/{len(variations)}: query='{query}'")

        while page <= max_pages_per_var:
            b_offset = (page - 1) * 10 + 1
            encoded_query = urllib.parse.quote(query)
            url = f"https://search.yahoo.com/search?p={encoded_query}&b={b_offset}"

            try:
                status, html = await asyncio.to_thread(_yahoo_get_sync, url)
                if status != 200:
                    print(f"  [Yahoo] '{keyword}' | v{v_idx} p{page} | HTTP {status} -- skipping variation")
                    break

                soup = BeautifulSoup(html, "html.parser")
                raw_hrefs: list[str] = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/RU=" in href:
                        try:
                            ru_part = href.split("/RU=")[1].split("/RK=")[0]
                            clean_target = urllib.parse.unquote(ru_part)
                            if clean_target.startswith("http"):
                                raw_hrefs.append(clean_target)
                        except Exception:
                            pass
                    elif href.startswith("http") and not any(
                        domain in href
                        for domain in [
                            "yahoo.com",
                            "yimg.com",
                            "yahoo.net",
                            "search.yahoo",
                        ]
                    ):
                        raw_hrefs.append(href)

                new_on_page = 0
                for u in raw_hrefs:
                    if u not in seen_urls:
                        seen_urls.add(u)
                        all_urls.append(u)
                        new_on_page += 1

                print(f"  [Yahoo] '{keyword}' | v{v_idx} p{page} (b={b_offset}) -> {new_on_page} new URLs | total: {len(all_urls)}")

                if new_on_page == 0:
                    stagnant_pages += 1
                    if stagnant_pages >= 3:
                        print(f"  [Yahoo] '{keyword}' | v{v_idx} exhausted -- next variation")
                        break
                else:
                    stagnant_pages = 0

                page += 1
                await asyncio.sleep(1.0)

            except Exception as e:
                print(f"  [Yahoo] '{keyword}' | v{v_idx} p{page} | error: {e}")
                break

    print(f"  [Yahoo] '{keyword}' DONE -- {len(all_urls)} URLs")
    return all_urls


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CRAWL ORCHESTRATOR — combines all engines per keyword
# ══════════════════════════════════════════════════════════════════════════════

async def crawl_keyword_all_engines(
    keyword: str,
    session: aiohttp.ClientSession,
    collection,
    all_domains: set,
    searxng_available: bool = False,
) -> int:
    """
    Crawl a single keyword using ALL available engines sequentially.
    Collects URLs from DuckDuckGo + Playwright/Chromium + SearXNG (if up).
    Saves domains to MongoDB incrementally.

    Returns total number of raw URLs collected.
    """
    print(f"\n{'='*60}")
    print(f"  KEYWORD: '{keyword}'")
    print(f"  Engines: {'DDG ' if USE_DUCKDUCKGO else ''}{'Playwright ' if USE_PLAYWRIGHT else ''}{'SearXNG' if searxng_available and USE_SEARXNG else ''}")
    print(f"  (Ctrl+C at any time to stop and save all progress)")
    print(f"{'='*60}")

    all_raw_urls: list[str] = []
    unsaved_domains: list[str] = []
    new_this_kw: set[str] = set()

    def process_urls(urls: list[str]):
        """Extract domains from URLs and auto-save when threshold reached."""
        nonlocal unsaved_domains
        for url in urls:
            domain = extract_domain(url)
            if domain and domain not in all_domains:
                all_domains.add(domain)
                new_this_kw.add(domain)
                unsaved_domains.append(domain)
        if len(unsaved_domains) >= SAVE_EVERY_N_DOMAINS:
            saved = save_domains_to_mongo(unsaved_domains, collection)
            print(f"  💾 Auto-saved {saved} new domains to MongoDB (batch of {len(unsaved_domains)})")
            unsaved_domains.clear()

    # ── Engine 1: DuckDuckGo ─────────────────────────────────────────────────
    if USE_DUCKDUCKGO:
        try:
            ddg_urls = await duckduckgo_crawl_keyword(keyword, session)
            all_raw_urls.extend(ddg_urls)
            process_urls(ddg_urls)
            print(f"  [DDG] Contributed {len(ddg_urls)} URLs for '{keyword}'")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  [DDG] Error on '{keyword}': {e}")

    # ── Engine 2: Playwright/Chromium (Bing) ──────────────────────────────────
    if USE_PLAYWRIGHT:
        try:
            pw_urls = await asyncio.to_thread(playwright_crawl_keyword_sync, keyword)
            all_raw_urls.extend(pw_urls)
            process_urls(pw_urls)
            print(f"  [PW] Contributed {len(pw_urls)} URLs for '{keyword}'")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  [PW] Error on '{keyword}': {e}")

    # ── Engine 3: SearXNG (only if Docker is running) ────────────────────────
    if USE_SEARXNG and searxng_available:
        try:
            sx_urls = await searxng_crawl_keyword(keyword, session)
            all_raw_urls.extend(sx_urls)
            process_urls(sx_urls)
            print(f"  [SearXNG] Contributed {len(sx_urls)} URLs for '{keyword}'")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  [SearXNG] Error on '{keyword}': {e}")

    # ── Engine 4: Yahoo Search ────────────────────────────────────────────────
    try:
        yh_urls = await yahoo_crawl_keyword(keyword)
        all_raw_urls.extend(yh_urls)
        process_urls(yh_urls)
        print(f"  [Yahoo] Contributed {len(yh_urls)} URLs for '{keyword}'")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"  [Yahoo] Error on '{keyword}': {e}")

    # ── Final flush of remaining unsaved domains ──────────────────────────────

    # ── Final flush of remaining unsaved domains ──────────────────────────────
    if unsaved_domains:
        saved = save_domains_to_mongo(unsaved_domains, collection)
        print(f"  [save] Final save: {saved} new domains for '{keyword}'")
        unsaved_domains.clear()

    total_urls = len(all_raw_urls)
    print(
        f"\n  [crawl] '{keyword}' COMPLETE | "
        f"total URLs: {total_urls} | "
        f"new domains this keyword: {len(new_this_kw)} | "
        f"all-time unique domains: {len(all_domains)}"
    )
    return total_urls


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API — run_search (called from main.py)
# ══════════════════════════════════════════════════════════════════════════════

async def run_search(
    keywords: list[str],
    concurrency: int = 1,   # sequential: fully exhaust each keyword before next
) -> dict:
    """
    Process all keywords ONE BY ONE, crawling EVERY available page on every
    engine for each keyword before moving to the next.

    Stopping conditions:
      a) All keywords fully exhausted (all engines returned no more results)
      b) User presses Ctrl+C  → saves everything collected so far

    Args:
        keywords:    List of search keyword strings.
        concurrency: Unused (kept for API compatibility). Always sequential.

    Returns:
        {"total_urls": int, "unique_domains": int, "new_inserted": int}
    """
    if not keywords:
        print("[searxng_search] No keywords provided.")
        return {"total_urls": 0, "unique_domains": 0, "new_inserted": 0}

    # Check if SearXNG Docker is available (non-blocking — we have other engines)
    searxng_available = False
    if USE_SEARXNG:
        searxng_available = start_searxng_docker()
        if not searxng_available:
            print("[searxng_search] SearXNG not available — will use DDG + Playwright only")

    collection, mongo_client = get_mongo_collection()
    all_domains: set[str] = set()
    total_urls = 0

    print(f"\n{'#'*60}")
    print(f"[searxng_search] UNLIMITED MULTI-ENGINE CRAWL")
    print(f"[searxng_search] Keywords to process: {len(keywords)}")
    print(f"[searxng_search] Engines active:")
    print(f"  * DuckDuckGo (direct):       {'YES' if USE_DUCKDUCKGO else 'NO'}")
    print(f"  * Playwright/Chromium:        {'YES' if USE_PLAYWRIGHT else 'NO'}")
    print(f"  * SearXNG (Docker):           {'YES' if searxng_available else 'NO (not running)'}")
    print(f"[searxng_search] Auto-save every {SAVE_EVERY_N_DOMAINS} domains | delay {PAGE_DELAY_SECONDS}s/page")
    print(f"[searxng_search] Press Ctrl+C at ANY TIME to stop and save")
    print(f"{'#'*60}\n")

    try:
        async with aiohttp.ClientSession() as session:
            for idx, keyword in enumerate(keywords, 1):
                print(f"\n[searxng_search] -- Keyword {idx}/{len(keywords)}: '{keyword}' --")
                try:
                    urls_found = await crawl_keyword_all_engines(
                        keyword=keyword,
                        session=session,
                        collection=collection,
                        all_domains=all_domains,
                        searxng_available=searxng_available,
                    )
                    total_urls += urls_found
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"  [searxng_search] Error on '{keyword}': {e} -- moving to next keyword")

    except KeyboardInterrupt:
        print(f"\n\n{'!'*60}")
        print("[searxng_search] Ctrl+C -- saving all collected data before exit...")
        print(f"{'!'*60}")

    # Final save (catches anything not flushed incrementally)
    print(f"\n[searxng_search] -- FINAL RESULTS --")
    print(f"[searxng_search] Total raw URLs collected : {total_urls}")
    print(f"[searxng_search] Unique domains extracted : {len(all_domains)}")

    final_inserted = save_domains_to_mongo(list(all_domains), collection)
    mongo_client.close()

    print(f"[searxng_search] New domains in MongoDB   : {final_inserted}")
    print(f"[searxng_search] Already in DB (skipped)  : {len(all_domains) - final_inserted}")

    return {
        "total_urls": total_urls,
        "unique_domains": len(all_domains),
        "new_inserted": final_inserted,
    }
