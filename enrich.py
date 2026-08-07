"""
enrich.py — Stage 6 & 7: Liveness check & Deep Intelligence Gathering (WHOIS, SSL, Headers, Tech Stack, Redirects).
Outputs to data/site_intelligence.csv
"""

import csv
import datetime
import json
import logging
import socket
import ssl
import concurrent.futures
from typing import List, Dict, Tuple, Optional
import requests
from bs4 import BeautifulSoup
import whois

import config

logging.basicConfig(
    filename=config.LOGS_DIR / "enrichment.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def get_ssl_info(domain: str) -> Dict[str, str]:
    """
    Connects via SSL socket to extract certificate details.
    """
    ssl_data = {"ssl_issuer": "", "ssl_valid_from": "", "ssl_valid_to": ""}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                if cert:
                    issuer_dict = dict(x[0] for x in cert.get("issuer", []))
                    ssl_data["ssl_issuer"] = issuer_dict.get("organizationName") or issuer_dict.get("commonName") or ""
                    ssl_data["ssl_valid_from"] = cert.get("notBefore", "")
                    ssl_data["ssl_valid_to"] = cert.get("notAfter", "")
    except Exception as e:
        logging.debug(f"SSL certificate fetch failed for {domain}: {e}")

    return ssl_data


def get_whois_info(domain: str) -> Dict[str, str]:
    """
    Queries WHOIS registration information.
    """
    whois_data = {"whois_registrar": "", "whois_created_date": "", "whois_expiry_date": ""}
    try:
        w = whois.whois(domain)
        if w:
            whois_data["whois_registrar"] = str(w.registrar) if w.registrar else ""
            
            created = w.creation_date
            if isinstance(created, list):
                created = created[0]
            whois_data["whois_created_date"] = str(created) if created else ""

            expiry = w.expiration_date
            if isinstance(expiry, list):
                expiry = expiry[0]
            whois_data["whois_expiry_date"] = str(expiry) if expiry else ""
    except Exception as e:
        logging.debug(f"WHOIS lookup failed for {domain}: {e}")

    return whois_data


def fingerprint_technologies(resp: requests.Response, html_soup: BeautifulSoup) -> List[str]:
    """
    Simple tech stack fingerprinting based on headers and HTML content.
    """
    techs = set()
    headers = resp.headers

    # Server header
    server = headers.get("Server", "")
    if "nginx" in server.lower():
        techs.add("Nginx")
    if "apache" in server.lower():
        techs.add("Apache")
    if "cloudflare" in server.lower():
        techs.add("Cloudflare")
    if "litespeed" in server.lower():
        techs.add("LiteSpeed")

    # X-Powered-By header
    powered = headers.get("X-Powered-By", "")
    if "php" in powered.lower():
        techs.add("PHP")
    if "express" in powered.lower():
        techs.add("Express.js")
    if "asp.net" in powered.lower():
        techs.add("ASP.NET")

    # HTML meta tags & scripts
    if html_soup:
        html_str = str(html_soup).lower()
        if "wp-content" in html_str or "wordpress" in html_str:
            techs.add("WordPress")
        if "react" in html_str or "_next" in html_str:
            techs.add("React / Next.js")
        if "vue" in html_str or "_nuxt" in html_str:
            techs.add("Vue.js / Nuxt")
        if "bootstrap" in html_str:
            techs.add("Bootstrap")
        if "jquery" in html_str:
            techs.add("jQuery")

    return sorted(list(techs))


def enrich_domain(domain: str, ip: str = "") -> Dict:
    """
    Performs full deep enrichment on a target domain.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Defaults
    record = {
        "domain": domain,
        "ip": ip,
        "status_code": "0",
        "is_active": False,
        "title": "",
        "meta_description": "",
        "meta_keywords": "",
        "ssl_issuer": "",
        "ssl_valid_from": "",
        "ssl_valid_to": "",
        "whois_registrar": "",
        "whois_created_date": "",
        "whois_expiry_date": "",
        "technologies": "",
        "http_headers": "",
        "redirect_chain": "",
        "screenshot_path": f"screenshots/{domain}.png",
        "last_checked": now_iso
    }

    # Fetch HTTP / Redirect chain
    target_url = f"https://{domain}"
    try:
        resp = requests.get(
            target_url,
            headers=config.HTTP_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
            allow_redirects=True
        )
    except requests.exceptions.RequestException:
        # Fallback to HTTP
        target_url = f"http://{domain}"
        try:
            resp = requests.get(
                target_url,
                headers=config.HTTP_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=True
            )
        except requests.exceptions.RequestException:
            resp = None

    if resp is not None:
        record["status_code"] = str(resp.status_code)
        record["is_active"] = resp.status_code < 400
        
        # Redirect chain
        chain = [r.url for r in resp.history] + [resp.url]
        record["redirect_chain"] = " -> ".join(chain)
        
        # Headers JSON
        headers_dict = dict(resp.headers)
        record["http_headers"] = json.dumps(headers_dict)

        # Parse HTML meta
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            record["title"] = soup.title.string.strip() if soup.title and soup.title.string else ""
            
            meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if meta_desc and meta_desc.get("content"):
                record["meta_description"] = meta_desc["content"].strip()

            meta_kw = soup.find("meta", attrs={"name": "keywords"})
            if meta_kw and meta_kw.get("content"):
                record["meta_keywords"] = meta_kw["content"].strip()

            techs = fingerprint_technologies(resp, soup)
            record["technologies"] = ";".join(techs)
        except Exception as e:
            logging.debug(f"HTML parsing error for {domain}: {e}")

    # SSL Cert Info
    ssl_info = get_ssl_info(domain)
    record.update(ssl_info)

    # WHOIS Info
    whois_info = get_whois_info(domain)
    record.update(whois_info)

    return record


def run_enrichment(domains_with_ip: List[Tuple[str, str]] = None) -> List[Dict]:
    """
    Runs enrichment pipeline across domain targets.
    """
    if domains_with_ip is None:
        domains_with_ip = []
        if config.IP_MAPPING_CSV.exists():
            with open(config.IP_MAPPING_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain"):
                        domains_with_ip.append((row["domain"].strip(), row.get("resolved_ip", "")))
        elif config.CLASSIFIED_CSV.exists():
            with open(config.CLASSIFIED_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("domain") and row.get("is_gambling") == "True":
                        domains_with_ip.append((row["domain"].strip(), ""))

    domains_with_ip = list(dict.fromkeys(domains_with_ip))

    print(f"[*] Starting Deep Intelligence Enrichment for {len(domains_with_ip)} domains...")
    logging.info(f"Starting enrichment for {len(domains_with_ip)} domains.")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.CONCURRENT_THREADS) as executor:
        future_to_domain = {executor.submit(enrich_domain, domain, ip): domain for domain, ip in domains_with_ip}
        for future in concurrent.futures.as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                res = future.result()
                results.append(res)
                print(f"  [ENRICHED] {domain} | Status: {res['status_code']} | Registrar: {res['whois_registrar'] or 'N/A'} | SSL: {res['ssl_issuer'] or 'N/A'}")
            except Exception as e:
                logging.error(f"Error enriching domain {domain}: {e}")

    # Write to site_intelligence.csv
    fieldnames = [
        "domain", "ip", "status_code", "is_active", "title", "meta_description",
        "meta_keywords", "ssl_issuer", "ssl_valid_from", "ssl_valid_to",
        "whois_registrar", "whois_created_date", "whois_expiry_date",
        "technologies", "http_headers", "redirect_chain", "screenshot_path", "last_checked"
    ]
    with open(config.SITE_INTEL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in results:
            writer.writerow(record)

    print(f"[+] Intelligence enrichment complete. Saved results to {config.SITE_INTEL_CSV}")
    logging.info(f"Enrichment finished. Saved {len(results)} records.")

    return results


if __name__ == "__main__":
    run_enrichment()
