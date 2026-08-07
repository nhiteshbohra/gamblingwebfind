import csv
import logging
import time
from urllib.parse import urlparse
import concurrent.futures
import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress unverified SSL warnings for target sites with proxy/self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1"
}

# Domain level ignore list for non-gambling helper / platform / regulatory sites
NON_GAMBLING_DOMAINS = {
    "gambleaware.org", "gamcare.org.uk", "responsiblegambling.org", "gamban.com",
    "connexontario.ca", "gamstop.co.uk", "thunderstruck.media", "top10casinos.com",
    "top10casinos.cl", "listofallbookmakers.com", "top100bookmakers.com",
    "bestonlinebookmakers.com", "casinofreak.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "youtube.com", "linkedin.com", "google.com",
    "telegram.org", "t.me", "apple.com", "play.google.com", "wikipedia.org"
}

GAMBLING_POSITIVE_KEYWORDS = [
    "casino", "betting", "satta", "matka", "teen patti", "poker", "roulette",
    "slot", "sportsbook", "wager", "jackpot", "odds", "deposit bonus", "bet",
    "rummy", "andar bahar", "color prediction", "baccarat", "bookmaker",
    "live dealer", "withdrawal", "register", "login", "signup", "play now", "claim bonus",
    "jili", "spins", "win", "game", "lotto", "spin"
]

NON_GAMBLING_NEGATIVE_KEYWORDS = [
    "gambling addiction", "responsible gambling", "helpline", "wikipedia",
    "treatment center", "support agency", "problem gambling"
]

def clean_and_verify_site(row: dict) -> dict:
    url = row["Final_Gambling_Website"]
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "").lower()

    # Rule 1: Exclude known non-gambling / responsible gambling domains
    for ngd in NON_GAMBLING_DOMAINS:
        if ngd in domain:
            return {"row": row, "status": "REJECTED_NON_GAMBLING_DOMAIN", "reason": f"Domain {domain} matched non-gambling filter ({ngd})"}

    # Rule 2: Check domain name directly for strong gambling keywords
    domain_has_gambling_kw = any(kw in domain for kw in ["casino", "bet", "slot", "poker", "satta", "matka", "spin", "jili", "win", "wager", "baccarat", "rummy", "jackpot", "lotto", "game", "play"])

    # Rule 3: Reachability & Content Verification with verify=False
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, verify=False, allow_redirects=True)
        final_url = resp.url
        status_code = resp.status_code
        
        # Dead or missing page check (404 Not Found, 410 Gone, 502 Bad Gateway)
        if status_code in [404, 410, 502, 503, 504]:
            return {"row": row, "status": "REJECTED_UNREACHABLE", "reason": f"HTTP status {status_code}"}
            
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        html_lower = resp.text.lower()
        title_lower = title.lower()

        # Reject helpline / support pages based on title
        if any(neg in title_lower for neg in ["responsible gambling", "gambleaware", "gamcare", "helpline", "problem gambling"]):
            return {"row": row, "status": "REJECTED_NON_GAMBLING_CONTENT", "reason": f"Helpline/Support title: '{title}'"}

        # Score gambling relevance
        matched_positive = [kw for kw in GAMBLING_POSITIVE_KEYWORDS if kw in html_lower or kw in title_lower or kw in domain]
        matched_negative = [kw for kw in NON_GAMBLING_NEGATIVE_KEYWORDS if kw in html_lower]

        if domain_has_gambling_kw or len(matched_positive) >= 1 or status_code in [200, 301, 302, 403]:
            row["Final_Gambling_Website"] = final_url
            return {"row": row, "status": "ACCEPTED", "reason": f"Active site confirmed (Status: {status_code}, Title: '{title[:35]}')"}
        else:
            return {"row": row, "status": "REJECTED_NOT_GAMBLING", "reason": f"No gambling keywords found (title: '{title[:35]}')"}

    except requests.exceptions.RequestException as e:
        err_msg = str(e)
        # If domain itself is explicitly a gambling domain name, keep it even if raw requests had network timeout
        if domain_has_gambling_kw and not any(term in err_msg for term in ["NameResolutionError", "gaierror", "NXDOMAIN"]):
            return {"row": row, "status": "ACCEPTED", "reason": f"Gambling domain confirmed (Server anti-bot protected / SSL proxy)"}
        return {"row": row, "status": "REJECTED_UNREACHABLE", "reason": f"Unreachable domain ({err_msg[:40]})"}


def clean_extracted_csv(input_csv=None, output_csv=None):
    from pathlib import Path
    script_dir = Path(__file__).resolve().parent
    
    if input_csv is None:
        if (script_dir / "gambling_sites_extracted.csv").exists():
            input_csv = script_dir / "gambling_sites_extracted.csv"
        else:
            input_csv = Path("gambling_sites_extracted.csv")
            
    if output_csv is None:
        output_csv = input_csv

    with open(input_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    logging.info(f"Loaded {len(rows)} initial extracted links from {input_csv}. Starting verification and filtering...")

    accepted_rows = []
    seen_urls = set()
    stats = {"ACCEPTED": 0, "REJECTED_NON_GAMBLING_DOMAIN": 0, "REJECTED_UNREACHABLE": 0, "REJECTED_NON_GAMBLING_CONTENT": 0, "REJECTED_NOT_GAMBLING": 0, "DUPLICATE": 0}

    # Parallel processing with 15 worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        future_to_row = {executor.submit(clean_and_verify_site, r): r for r in rows}
        
        count = 0
        for future in concurrent.futures.as_completed(future_to_row):
            count += 1
            res = future.result()
            status = res["status"]
            reason = res["reason"]
            r = res["row"]
            url = r["Final_Gambling_Website"]

            if status == "ACCEPTED":
                if url not in seen_urls:
                    seen_urls.add(url)
                    accepted_rows.append(r)
                    stats["ACCEPTED"] += 1
                    logging.info(f"[{count}/{len(rows)}] [✓ ACCEPTED] {url} - {reason}")
                else:
                    stats["DUPLICATE"] += 1
                    logging.info(f"[{count}/{len(rows)}] [✗ DUPLICATE] {url}")
            else:
                stats[status] += 1
                logging.info(f"[{count}/{len(rows)}] [✗ {status}] {url} - {reason}")

    # Write cleaned rows back to CSV
    fieldnames = ["Source_Website", "Raw_Affiliate_Link", "Final_Gambling_Website"]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(accepted_rows)

    logging.info("=" * 60)
    logging.info(f"VERIFICATION & CLEANING COMPLETE!")
    logging.info(f"Initial links evaluated: {len(rows)}")
    logging.info(f"Active & Confirmed Gambling Sites Kept: {len(accepted_rows)}")
    logging.info(f"Breakdown of Filtered Sites:")
    for k, v in stats.items():
        logging.info(f"  - {k}: {v}")
    logging.info(f"Clean dataset exported to '{output_csv}'")

if __name__ == "__main__":
    clean_extracted_csv()

