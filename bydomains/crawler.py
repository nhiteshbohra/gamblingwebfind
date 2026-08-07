import csv
import logging
import re
import time
from urllib.parse import urljoin, urlparse
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

# Setup Logging to track progress in real-time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# Target directory websites
TARGET_URLS = [
    "https://www.casinofreak.com/reviews/all-online-casinos",
    "https://www.top10casinos.com/casino-list/index.html",
    "https://listofallbookmakers.com/bookmaker-licenses-list",
    "https://www.top100bookmakers.com/completelist.php",
    "https://bestonlinebookmakers.com/top-bookmakers-rating.html"
]

# Non-gambling domains, social platforms, and CDNs to skip
IGNORED_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "linkedin.com", "pinterest.com", "google.com", "googletagmanager.com",
    "cloudflare.com", "t.me", "telegram.org", "apple.com", "play.google.com",
    "wordpress.org", "w3.org"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def is_valid_external_link(base_url: str, target_url: str) -> bool:
    """Filters out internal site links, invalid protocols, and social media/utility domains."""
    if not target_url or not target_url.startswith("http"):
        return False
        
    base_domain = urlparse(base_url).netloc.replace("www.", "")
    target_domain = urlparse(target_url).netloc.replace("www.", "")
    
    # Exclude self-referencing/internal domain links
    if not target_domain or target_domain == base_domain:
        return False
        
    # Exclude non-gambling social/analytics domains
    for ignored in IGNORED_DOMAINS:
        if ignored in target_domain:
            return False
            
    return True


def resolve_final_destination(redirect_url: str) -> str:
    """Follows affiliate redirects (e.g., site.com/visit/casino) to find the actual casino URL."""
    try:
        response = requests.head(
            redirect_url, 
            headers=HEADERS, 
            allow_redirects=True, 
            timeout=10
        )
        return response.url
    except requests.RequestException:
        # Fallback to GET stream if HEAD is blocked by the target server
        try:
            response = requests.get(
                redirect_url, 
                headers=HEADERS, 
                allow_redirects=True, 
                timeout=10, 
                stream=True
            )
            return response.url
        except requests.RequestException:
            return redirect_url


def extract_links_with_playwright(page_url: str) -> list:
    """Launches headless Chromium, handles JS execution/scrolling, and extracts all anchor tags."""
    logging.info(f"Loading page: {page_url}")
    found_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()

        try:
            # Navigate to page and wait for initial network activity to idle
            page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            
            # Scroll to trigger lazy-loaded gambling lists
            for _ in range(4):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1000)

            # Retrieve all dynamic <a> tag href attributes
            raw_hrefs = page.eval_on_selector_all("a[href]", "elements => elements.map(e => e.href)")
            
            for href in raw_hrefs:
                full_url = urljoin(page_url, href.strip())
                if is_valid_external_link(page_url, full_url):
                    found_urls.add(full_url)

        except PlaywrightTimeoutError:
            logging.error(f"Timeout loading {page_url}. Capturing whatever links rendered so far...")
        except Exception as e:
            logging.error(f"Error scraping {page_url}: {e}")
        finally:
            browser.close()

    return list(found_urls)


def run_crawler(output_filename: str = "gambling_sites_extracted.csv"):
    all_results = []
    seen_final_urls = set()

    for index, source_url in enumerate(TARGET_URLS, 1):
        logging.info(f"[{index}/{len(TARGET_URLS)}] Scraping Directory: {source_url}")
        
        extracted_links = extract_links_with_playwright(source_url)
        logging.info(f"Extracted {len(extracted_links)} outbound candidate links. Unmasking redirects...")

        for idx, link in enumerate(extracted_links, 1):
            logging.info(f"  -> ({idx}/{len(extracted_links)}) Resolving: {link}")
            final_url = resolve_final_destination(link)
            
            # Deduplicate results based on final URL
            if final_url not in seen_final_urls:
                seen_final_urls.add(final_url)
                all_results.append({
                    "Source_Website": source_url,
                    "Raw_Affiliate_Link": link,
                    "Final_Gambling_Website": final_url
                })
            
            time.sleep(0.3)  # Gentle request pacing

        time.sleep(2)  # Pause between target websites

    # Save gathered dataset to CSV
    if all_results:
        fieldnames = ["Source_Website", "Raw_Affiliate_Link", "Final_Gambling_Website"]
        with open(output_filename, "w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        logging.info(f"Done! Successfully exported {len(all_results)} links to '{output_filename}'")
    else:
        logging.warning("No gambling links were found.")


if __name__ == "__main__":
    run_crawler()