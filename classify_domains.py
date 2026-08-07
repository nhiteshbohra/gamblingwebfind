"""
classify_domains.py — Stage 3: Stage-1 crawl & rule-based classification scoring.
Outputs to data/classified_domains.csv
"""

import csv
import datetime
import logging
import concurrent.futures
from typing import List, Dict, Tuple, Optional
import requests
from bs4 import BeautifulSoup

import config

logging.basicConfig(
    filename=config.LOGS_DIR / "classification.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def fetch_homepage_content(domain: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Attempts HTTP/HTTPS fetch of domain homepage.
    Returns (status_code, title, html_text).
    """
    for scheme in ["https", "http"]:
        url = f"{scheme}://{domain}"
        try:
            resp = requests.get(
                url,
                headers=config.HTTP_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=True
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                return resp.status_code, title, resp.text.lower()
            elif resp.status_code in [403, 503, 301, 302]:
                # Still try to extract title if body received
                soup = BeautifulSoup(resp.text, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                return resp.status_code, title, resp.text.lower()
        except requests.exceptions.RequestException:
            continue

    return None, None, None


def classify_domain(domain: str) -> Dict:
    """
    Rule-based classification & accessibility scoring.
    Only classifies is_gambling = True if site is live, accessible in local region (HTTP 200), and verified gambling content.
    """
    status_code, title, html_body = fetch_homepage_content(domain)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Blocked titles list
    blocked_titles = [
        "access denied", "attention required!", "403 forbidden", "404 not found",
        "502 bad gateway", "error: the request could not be satisfied", "domain suspended",
        "site blocked", "access restricted"
    ]

    title_lower = (title or "").lower()
    is_blocked_title = any(bt in title_lower for bt in blocked_titles)

    # 1. Accessibility Check: Must be HTTP 200 and NOT a Cloudflare/Access Denied block screen
    if not html_body or status_code != 200 or is_blocked_title:
        reason = "Failed to reach domain homepage"
        if status_code and status_code != 200:
            reason = f"Inaccessible / Blocked (HTTP {status_code})"
        elif is_blocked_title:
            reason = f"Inaccessible / Blocked page title ('{title}')"
        
        return {
            "domain": domain,
            "is_gambling": False,
            "confidence_score": 0.0,
            "title": title or "",
            "classification_reason": reason,
            "checked_at": now_iso
        }

    positive_matches = []
    negative_matches = []
    ui_features = []

    domain_lower = domain.lower()
    for kw in config.GAMBLING_POSITIVE_KEYWORDS:
        if kw in domain_lower:
            positive_matches.append(f"domain:{kw}")

    if html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        
        # 1. Inspect headings (H1, H2)
        headings = " ".join([h.get_text() for h in soup.find_all(["h1", "h2"])])
        for kw in config.GAMBLING_POSITIVE_KEYWORDS:
            if kw in headings.lower():
                positive_matches.append(f"heading:{kw}")

        # 2. Inspect buttons and interactive UI elements
        interactive_text = " ".join([
            elem.get_text() for elem in soup.find_all(["button", "a", "input", "span"])
            if elem.get_text()
        ]).lower()

        ui_triggers = ["login", "register", "signup", "deposit", "withdraw", "play now", "bet now", "claim bonus", "betting id"]
        for trigger in ui_triggers:
            if trigger in interactive_text:
                ui_features.append(trigger)

        # 3. Inspect full body text
        for kw in config.GAMBLING_POSITIVE_KEYWORDS:
            if kw in html_body and not any(m.endswith(f":{kw}") for m in positive_matches):
                positive_matches.append(kw)

        # 4. Check negative keywords
        for kw in config.NON_GAMBLING_NEGATIVE_KEYWORDS:
            if kw in html_body:
                negative_matches.append(kw)

    # Score calculation algorithm
    pos_score = min(len(positive_matches) * 0.15, 0.7)
    if ui_features:
        pos_score += min(len(ui_features) * 0.1, 0.2)

    neg_score = min(len(negative_matches) * 0.25, 0.8)

    # Title bonus
    if title:
        for kw in config.GAMBLING_POSITIVE_KEYWORDS:
            if kw in title_lower:
                pos_score += 0.2
                break

    confidence_score = max(0.0, min(1.0, round(pos_score - neg_score, 2)))
    is_gambling = confidence_score >= config.GAMBLING_CONFIDENCE_THRESHOLD

    reasons = []
    if positive_matches:
        reasons.append(f"Matched positive content: {', '.join(positive_matches[:5])}")
    if ui_features:
        reasons.append(f"Detected UI features: {', '.join(ui_features[:3])}")
    if negative_matches:
        reasons.append(f"Matched negative keywords: {', '.join(negative_matches[:3])}")
    if not reasons:
        reasons.append("No definitive gambling content found")

    classification_reason = " | ".join(reasons)

    return {
        "domain": domain,
        "is_gambling": is_gambling,
        "confidence_score": confidence_score,
        "title": title or "",
        "classification_reason": classification_reason,
        "checked_at": now_iso
    }


def run_classification(domains: List[str] = None) -> List[Dict]:
    """
    Runs classification across domain list using multi-threading.
    """
    if domains is None:
        # Load from discovered_domains.csv if exists
        domains = []
        if config.DISCOVERED_CSV.exists():
            with open(config.DISCOVERED_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain"):
                        domains.append(row["domain"].strip())

    # Deduplicate domains list
    domains = list(dict.fromkeys(domains))

    print(f"[*] Starting Classification on {len(domains)} domains...")
    logging.info(f"Starting classification on {len(domains)} domains.")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.CONCURRENT_THREADS) as executor:
        future_to_domain = {executor.submit(classify_domain, domain): domain for domain in domains}
        for future in concurrent.futures.as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                res = future.result()
                results.append(res)
                print(f"  [{'GAMBLING' if res['is_gambling'] else 'OTHER'}] {domain} (Score: {res['confidence_score']})")
            except Exception as e:
                logging.error(f"Error classifying domain {domain}: {e}")

    # Write to classified_domains.csv
    fieldnames = ["domain", "is_gambling", "confidence_score", "title", "classification_reason", "checked_at"]
    with open(config.CLASSIFIED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in results:
            writer.writerow(record)

    gambling_count = sum(1 for r in results if r["is_gambling"])
    print(f"[+] Classification complete. {gambling_count}/{len(results)} domains confirmed as gambling sites.")
    logging.info(f"Classification finished. Saved results to {config.CLASSIFIED_CSV}")

    return results


if __name__ == "__main__":
    run_classification()
