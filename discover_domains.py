"""
discover_domains.py — Stage 1 & 2: Query SearXNG / Fallback engines & directories to discover gambling domains.
Supports multi-page search pagination and directory scraper integration.
Outputs to data/discovered_domains.csv
"""

import csv
import datetime
import logging
import time
from typing import List, Dict, Set
from urllib.parse import parse_qs, urlparse
import requests
from bs4 import BeautifulSoup

import config
from domain_utils import normalize_domain, is_valid_domain
from directory_crawler import run_directory_crawler

logging.basicConfig(
    filename=config.LOGS_DIR / "discovery.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def load_keywords(filepath: str = None) -> List[str]:
    path = filepath or config.KEYWORDS_FILE
    if not path.exists():
        logging.warning(f"Keywords file not found at {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def load_dorks(filepath: str = None) -> List[str]:
    path = filepath or config.DORKS_FILE
    if not path.exists():
        logging.warning(f"Dorks file not found at {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def query_searxng(keyword: str, max_pages: int = config.MAX_SEARCH_PAGES, max_results: int = config.MAX_RESULTS_PER_KEYWORD) -> List[Dict]:
    """
    Query local SearXNG instance across multiple pages (JSON format with HTML fallback).
    """
    results = []
    for page_num in range(1, max_pages + 1):
        # 1. Try JSON query
        params = {"q": keyword, "format": "json", "pageno": page_num}
        try:
            resp = requests.get(
                f"{config.SEARXNG_URL}/search",
                params=params,
                headers=config.HTTP_HEADERS,
                timeout=config.REQUEST_TIMEOUT
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    page_results = data.get("results", [])
                    if not page_results:
                        break
                    for rank, item in enumerate(page_results, start=(page_num - 1) * 10 + 1):
                        url = item.get("url")
                        engine = item.get("engine", "searxng")
                        if url:
                            results.append({
                                "keyword": keyword,
                                "search_engine": engine,
                                "rank": rank,
                                "source_url": url
                            })
                        if len(results) >= max_results:
                            break
                except Exception:
                    pass

            # 2. If JSON returned 403 (disabled in settings.yml) or no results, fallback to HTML parsing
            if not results or resp.status_code == 403:
                html_params = {"q": keyword, "pageno": page_num}
                h_resp = requests.get(
                    f"{config.SEARXNG_URL}/search",
                    params=html_params,
                    headers=config.HTTP_HEADERS,
                    timeout=config.REQUEST_TIMEOUT
                )
                if h_resp.status_code == 200:
                    soup = BeautifulSoup(h_resp.text, "html.parser")
                    anchors = soup.select("article.result h3 a, h3 a, a.result__url")
                    for rank, a in enumerate(anchors, start=(page_num - 1) * 10 + 1):
                        href = a.get("href", "").strip()
                        if href and href.startswith("http"):
                            results.append({
                                "keyword": keyword,
                                "search_engine": "searxng_html",
                                "rank": rank,
                                "source_url": href
                            })
                        if len(results) >= max_results:
                            break
        except requests.exceptions.RequestException as e:
            logging.debug(f"SearXNG query exception for '{keyword}': {e}")
            break

        if len(results) >= max_results:
            break

    return results


def query_duckduckgo_fallback(keyword: str, max_pages: int = config.MAX_SEARCH_PAGES, max_results: int = config.MAX_RESULTS_PER_KEYWORD) -> List[Dict]:
    """
    Fallback search using DuckDuckGo HTML interface with pagination support.
    """
    results = []
    for page_num in range(1, max_pages + 1):
        try:
            url = "https://html.duckduckgo.com/html/"
            data = {"q": keyword, "s": str((page_num - 1) * 30)}
            headers = {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            resp = requests.post(url, data=data, headers=headers, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", class_="result__url")
                if not links:
                    break
                for rank, a in enumerate(links, start=(page_num - 1) * 10 + 1):
                    href = a.get("href", "").strip()
                    if href:
                        if href.startswith("//duckduckgo.com/l/?uddg="):
                            parsed = urlparse("https:" + href)
                            qs = parse_qs(parsed.query)
                            if "uddg" in qs:
                                href = qs["uddg"][0]
                        if href.startswith("http"):
                            results.append({
                                "keyword": keyword,
                                "search_engine": "duckduckgo_html",
                                "rank": rank,
                                "source_url": href
                            })
                    if len(results) >= max_results:
                        break
            else:
                break
        except Exception as e:
            logging.warning(f"DuckDuckGo fallback search failed for '{keyword}' (page {page_num}): {e}")
            break

        if len(results) >= max_results:
            break

    return results


def run_discovery(
    keywords: List[str] = None,
    include_dorks: bool = True,
    include_directory_scrape: bool = True,
    max_pages: int = config.MAX_SEARCH_PAGES,
    max_results: int = config.MAX_RESULTS_PER_KEYWORD
) -> Set[str]:
    """
    Runs multi-page search discovery over keywords & dorks, integrates directory scraper, normalizes domains, writes to CSV.
    """
    search_queries = []
    if keywords:
        search_queries.extend(keywords)
    else:
        search_queries.extend(load_keywords())

    if include_dorks:
        dorks = load_dorks()
        search_queries.extend(dorks)

    search_queries = list(dict.fromkeys(search_queries))

    print(f"[*] Starting Discovery phase using {len(search_queries)} total search queries (Pages: 1..{max_pages})...")
    logging.info(f"Starting discovery phase across {len(search_queries)} queries up to {max_pages} pages.")

    existing_domains: Set[str] = set()
    discovered_records = []
    
    csv_exists = config.DISCOVERED_CSV.exists()
    if csv_exists:
        try:
            with open(config.DISCOVERED_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain"):
                        existing_domains.add(row["domain"].strip().lower())
        except Exception as e:
            logging.error(f"Error reading existing discovered_domains.csv: {e}")

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Search Query Discovery
    for idx, query in enumerate(search_queries, start=1):
        print(f"  [{idx}/{len(search_queries)}] Searching query across {max_pages} pages: '{query}'...")
        raw_results = query_searxng(query, max_pages=max_pages, max_results=max_results)
        
        if not raw_results:
            raw_results = query_duckduckgo_fallback(query, max_pages=max_pages, max_results=max_results)

        for item in raw_results:
            source_url = item["source_url"]
            domain = normalize_domain(source_url)
            
            if domain and is_valid_domain(domain):
                if domain not in existing_domains:
                    existing_domains.add(domain)
                    discovered_records.append({
                        "domain": domain,
                        "keyword": item["keyword"],
                        "search_engine": item["search_engine"],
                        "rank": item["rank"],
                        "discovered_at": now_iso,
                        "source_url": source_url
                    })

    # 2. Directory Scraper Discovery
    if include_directory_scrape:
        print("[*] Running Directory Aggregator Scraper...")
        dir_domains = run_directory_crawler()
        for domain in dir_domains:
            if domain not in existing_domains:
                existing_domains.add(domain)
                discovered_records.append({
                    "domain": domain,
                    "keyword": "directory_aggregator",
                    "search_engine": "playwright_directory_crawler",
                    "rank": 1,
                    "discovered_at": now_iso,
                    "source_url": "directory_scraper"
                })

    # Write records to CSV
    fieldnames = ["domain", "keyword", "search_engine", "rank", "discovered_at", "source_url"]
    write_header = not csv_exists or config.DISCOVERED_CSV.stat().st_size == 0

    with open(config.DISCOVERED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for record in discovered_records:
            writer.writerow(record)

    print(f"[+] Discovery complete. Found {len(discovered_records)} new unique domains (Total: {len(existing_domains)}).")
    logging.info(f"Discovery complete. Added {len(discovered_records)} records to {config.DISCOVERED_CSV}")
    
    return existing_domains


if __name__ == "__main__":
    run_discovery()
