"""
resolve_ip.py — Stage 4 & 5: DNS resolution & co-hosted domain discovery via reverse-IP.
Outputs to data/ip_mapping.csv
"""

import csv
import datetime
import json
import logging
import socket
import concurrent.futures
from typing import List, Dict, Set, Tuple
import requests

import config

logging.basicConfig(
    filename=config.LOGS_DIR / "dns_resolution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def resolve_domain_ips(domain: str) -> List[str]:
    """
    Resolves IPv4 addresses for a domain name.
    """
    ips = set()
    try:
        infos = socket.getaddrinfo(domain, None)
        for info in infos:
            family, _, _, _, sockaddr = info
            if family == socket.AF_INET:
                ips.add(sockaddr[0])
    except Exception as e:
        logging.debug(f"DNS resolution failed for {domain}: {e}")
    return sorted(list(ips))


def get_ip_info(ip: str) -> Tuple[str, str]:
    """
    Fetches ASN and hosting provider for an IP address using ip-api.com (free tier).
    Returns (asn, hosting_provider).
    """
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,as,isp,org"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                asn = data.get("as", "")
                provider = data.get("org") or data.get("isp") or ""
                return asn, provider
    except Exception as e:
        logging.debug(f"IP info lookup failed for {ip}: {e}")
    return "", ""


def reverse_ip_hackertarget(ip: str) -> List[str]:
    """
    Reverse IP lookup via HackerTarget free API.
    """
    domains = set()
    try:
        url = f"https://api.hackertarget.com/reverseiplookup/?q={ip}"
        resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=10)
        if resp.status_code == 200 and "API count exceeded" not in resp.text and "error" not in resp.text:
            lines = resp.text.strip().split("\n")
            for line in lines:
                d = line.strip().lower()
                if d and "." in d:
                    domains.add(d)
    except Exception as e:
        logging.debug(f"HackerTarget lookup failed for {ip}: {e}")
    return sorted(list(domains))


def reverse_ip_rapiddns(ip: str) -> List[str]:
    """
    Reverse IP lookup via RapidDNS sameip endpoint.
    """
    domains = set()
    try:
        url = f"https://rapiddns.io/sameip/{ip}?full=1#result"
        resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=10)
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", id="table")
            if table:
                for row in table.find_all("tr")[1:]:
                    cols = row.find_all("td")
                    if cols:
                        d = cols[0].text.strip().lower()
                        if d and "." in d:
                            domains.add(d)
    except Exception as e:
        logging.debug(f"RapidDNS lookup failed for {ip}: {e}")
    return sorted(list(domains))


def discover_cohosted_domains(ip: str) -> List[str]:
    """
    Aggregates reverse-IP results from multiple free providers.
    """
    cohosted = set()
    cohosted.update(reverse_ip_hackertarget(ip))
    cohosted.update(reverse_ip_rapiddns(ip))
    return sorted(list(cohosted))


def process_domain_ip_mapping(domain: str) -> Dict:
    """
    Resolves domain -> IP -> ASN/Provider -> Co-hosted domains.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ips = resolve_domain_ips(domain)
    
    if not ips:
        return {
            "domain": domain,
            "resolved_ip": "UNRESOLVED",
            "asn": "",
            "hosting_provider": "",
            "co_hosted_domains": "",
            "checked_at": now_iso
        }

    primary_ip = ips[0]
    asn, provider = get_ip_info(primary_ip)
    cohosted = discover_cohosted_domains(primary_ip)
    
    # Filter out self domain from co-hosted list
    cohosted_filtered = [d for d in cohosted if d != domain]

    return {
        "domain": domain,
        "resolved_ip": ";".join(ips),
        "asn": asn,
        "hosting_provider": provider,
        "co_hosted_domains": ";".join(cohosted_filtered[:50]),  # cap at top 50
        "checked_at": now_iso
    }


def run_ip_resolution(domains: List[str] = None) -> List[Dict]:
    """
    Runs IP resolution and co-hosted domain discovery across target domains.
    """
    if domains is None:
        domains = []
        if config.CLASSIFIED_CSV.exists():
            with open(config.CLASSIFIED_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Filter for confirmed gambling sites or resolve all
                    if row.get("domain") and row.get("is_gambling") == "True":
                        domains.append(row["domain"].strip())

    domains = list(dict.fromkeys(domains))

    print(f"[*] Starting IP Resolution & Reverse-IP Discovery for {len(domains)} confirmed domains...")
    logging.info(f"Starting IP resolution for {len(domains)} domains.")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.CONCURRENT_THREADS) as executor:
        future_to_domain = {executor.submit(process_domain_ip_mapping, domain): domain for domain in domains}
        for future in concurrent.futures.as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                res = future.result()
                results.append(res)
                print(f"  [RESOLVED] {domain} -> {res['resolved_ip']} ({res['hosting_provider'] or 'Unknown ISP'}) | Co-hosted: {len(res['co_hosted_domains'].split(';')) if res['co_hosted_domains'] else 0}")
            except Exception as e:
                logging.error(f"Error resolving IP for domain {domain}: {e}")

    # Write to ip_mapping.csv
    fieldnames = ["domain", "resolved_ip", "asn", "hosting_provider", "co_hosted_domains", "checked_at"]
    with open(config.IP_MAPPING_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in results:
            writer.writerow(record)

    print(f"[+] IP Resolution complete. Saved results to {config.IP_MAPPING_CSV}")
    logging.info(f"IP Resolution finished. Saved {len(results)} records.")

    return results


if __name__ == "__main__":
    run_ip_resolution()
