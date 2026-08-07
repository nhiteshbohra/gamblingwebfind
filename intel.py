#!/usr/bin/env python3
"""
intel.py — Gather intelligence on live domains: WHOIS, HTTP headers,
page content, SSL certificates, and IP geolocation.

Usage:
    python intel.py --domain example.com
    python intel.py --input live_domains.txt -o intel_results.json
"""

import argparse
import json
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime

import requests
from bs4 import BeautifulSoup

try:
    import whois as python_whois  # type: ignore[import-not-found]
except ImportError:
    python_whois = None

import config
import utils

logger = utils.setup_logging("intel")


@dataclass
class DomainIntel:
    domain: str
    ip: str = ""
    # HTTP info
    http_code: int = 0
    final_url: str = ""
    response_time_ms: int = 0
    server: str = ""
    content_language: str = ""
    # Page content
    page_title: str = ""
    meta_description: str = ""
    meta_keywords: str = ""
    # Technologies
    technologies: str = ""
    # SSL
    ssl_issuer: str = ""
    ssl_expiry: str = ""
    # WHOIS
    whois_registrar: str = ""
    whois_created: str = ""
    whois_expires: str = ""
    whois_country: str = ""
    # Geo IP
    ip_country: str = ""
    ip_city: str = ""
    ip_isp: str = ""
    # Errors
    errors: list = field(default_factory=list)


def resolve_ip(domain: str) -> str:
    """Resolve domain to its primary IPv4 address."""
    try:
        return socket.gethostbyname(domain)
    except socket.gaierror:
        return ""


def gather_http_intel(domain: str, session: requests.Session) -> dict:
    """Fetch the homepage and extract HTTP-level intelligence."""
    data = {
        "http_code": 0,
        "final_url": "",
        "response_time_ms": 0,
        "server": "",
        "page_title": "",
        "meta_description": "",
        "meta_keywords": "",
        "content_language": "",
        "technologies": "",
        "html": "",
    }

    for scheme in ["https", "http"]:
        url = f"{scheme}://{domain}"
        try:
            start = time.time()
            resp = session.get(url, timeout=config.DEFAULT_TIMEOUT, verify=False, allow_redirects=True)
            elapsed_ms = int((time.time() - start) * 1000)

            data["http_code"] = resp.status_code
            data["final_url"] = resp.url
            data["response_time_ms"] = elapsed_ms
            data["server"] = resp.headers.get("Server", "")
            data["html"] = resp.text

            # Detect technologies from headers
            techs = []
            headers_lower = {k.lower(): v for k, v in resp.headers.items()}
            if "x-powered-by" in headers_lower:
                techs.append(headers_lower["x-powered-by"])
            if "cf-ray" in headers_lower or "cf-cache-status" in headers_lower:
                techs.append("Cloudflare")
            if "x-amz" in str(headers_lower):
                techs.append("AWS")
            if resp.headers.get("Server", "").lower().startswith("nginx"):
                techs.append("nginx")
            elif resp.headers.get("Server", "").lower().startswith("apache"):
                techs.append("Apache")
            elif "litespeed" in resp.headers.get("Server", "").lower():
                techs.append("LiteSpeed")

            # Parse HTML
            try:
                soup = BeautifulSoup(resp.text, "html.parser")

                # Title
                if soup.title and soup.title.string:
                    data["page_title"] = soup.title.string.strip()[:500]

                # Meta description
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    data["meta_description"] = meta_desc["content"].strip()[:500]

                # Meta keywords
                meta_kw = soup.find("meta", attrs={"name": "keywords"})
                if meta_kw and meta_kw.get("content"):
                    data["meta_keywords"] = meta_kw["content"].strip()[:500]

                # Content language
                html_tag = soup.find("html")
                if html_tag and html_tag.get("lang"):
                    data["content_language"] = html_tag["lang"]

                # Additional tech detection from HTML
                page_text = resp.text.lower()
                if "wp-content" in page_text or "wordpress" in page_text:
                    techs.append("WordPress")
                if "jquery" in page_text:
                    techs.append("jQuery")
                if "react" in page_text or "reactdom" in page_text:
                    techs.append("React")
                if "bootstrap" in page_text:
                    techs.append("Bootstrap")

            except Exception as e:
                logger.debug(f"HTML parse error for {domain}: {e}")

            data["technologies"] = ", ".join(sorted(set(techs)))
            return data

        except requests.exceptions.SSLError:
            if scheme == "https":
                continue
        except requests.exceptions.RequestException:
            if scheme == "https":
                continue

    return data


def gather_ssl_info(domain: str) -> dict:
    """Get SSL certificate information."""
    data = {"ssl_issuer": "", "ssl_expiry": ""}
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(10)
            s.connect((domain, 443))
            cert = s.getpeercert()

            # Issuer
            issuer_parts = dict(x[0] for x in cert.get("issuer", []))
            data["ssl_issuer"] = issuer_parts.get("organizationName", issuer_parts.get("commonName", ""))

            # Expiry
            not_after = cert.get("notAfter", "")
            if not_after:
                data["ssl_expiry"] = not_after

    except Exception as e:
        logger.debug(f"SSL info failed for {domain}: {e}")

    return data


def gather_whois_info(domain: str) -> dict:
    """Get WHOIS registration information."""
    data = {
        "whois_registrar": "",
        "whois_created": "",
        "whois_expires": "",
        "whois_country": "",
    }

    if python_whois is None:
        logger.debug("python-whois not installed, skipping WHOIS lookup")
        return data

    try:
        w = python_whois.whois(domain)

        data["whois_registrar"] = str(w.registrar or "")

        # Creation date
        created = w.creation_date
        if isinstance(created, list):
            created = created[0]
        if created:
            data["whois_created"] = str(created)

        # Expiry date
        expires = w.expiration_date
        if isinstance(expires, list):
            expires = expires[0]
        if expires:
            data["whois_expires"] = str(expires)

        # Country
        data["whois_country"] = str(w.country or "")

    except Exception as e:
        logger.debug(f"WHOIS failed for {domain}: {e}")

    return data


def gather_geo_info(ip: str, session: requests.Session) -> dict:
    """Get IP geolocation from ip-api.com."""
    data = {"ip_country": "", "ip_city": "", "ip_isp": ""}

    if not ip:
        return data

    try:
        url = config.GEO_IP_API.format(ip=ip)
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            geo = resp.json()
            if geo.get("status") == "success":
                data["ip_country"] = geo.get("country", "")
                data["ip_city"] = geo.get("city", "")
                data["ip_isp"] = geo.get("isp", "")
    except Exception as e:
        logger.debug(f"Geo IP failed for {ip}: {e}")

    return data


def gather_intel(domain: str, session: requests.Session | None = None) -> DomainIntel:
    """
    Gather all intelligence on a single domain.
    Combines HTTP, SSL, WHOIS, and Geo IP data.
    """
    if session is None:
        session = utils.create_session()

    intel = DomainIntel(domain=domain)
    logger.info(f"📊 Gathering intel: {domain}")

    # 1. Resolve IP
    intel.ip = resolve_ip(domain)

    # 2. HTTP intel (page title, headers, tech, meta)
    http_data = gather_http_intel(domain, session)
    intel.http_code = http_data["http_code"]
    intel.final_url = http_data["final_url"]
    intel.response_time_ms = http_data["response_time_ms"]
    intel.server = http_data["server"]
    intel.page_title = http_data["page_title"]
    intel.meta_description = http_data["meta_description"]
    intel.meta_keywords = http_data["meta_keywords"]
    intel.content_language = http_data["content_language"]
    intel.technologies = http_data["technologies"]

    # 3. SSL certificate info
    ssl_data = gather_ssl_info(domain)
    intel.ssl_issuer = ssl_data["ssl_issuer"]
    intel.ssl_expiry = ssl_data["ssl_expiry"]

    # 4. WHOIS info
    whois_data = gather_whois_info(domain)
    intel.whois_registrar = whois_data["whois_registrar"]
    intel.whois_created = whois_data["whois_created"]
    intel.whois_expires = whois_data["whois_expires"]
    intel.whois_country = whois_data["whois_country"]

    # 5. Geo IP info
    geo_data = gather_geo_info(intel.ip, session)
    intel.ip_country = geo_data["ip_country"]
    intel.ip_city = geo_data["ip_city"]
    intel.ip_isp = geo_data["ip_isp"]

    logger.info(
        f"  ✅ {domain}: title='{intel.page_title[:60]}...' | "
        f"ip={intel.ip} ({intel.ip_country}) | server={intel.server}"
    )
    return intel


def gather_intel_batch(
    domains: list[str],
    delay: float = config.REQUEST_DELAY,
) -> list[DomainIntel]:
    """Gather intelligence on a batch of domains sequentially (with rate limiting)."""
    # Suppress InsecureRequestWarning
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    results = []
    session = utils.create_session()
    total = len(domains)

    for i, domain in enumerate(domains, 1):
        logger.info(f"[{i}/{total}] Processing {domain}...")
        intel = gather_intel(domain, session)
        results.append(intel)

        if i < total:
            utils.rate_sleep(delay)

    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    parser = argparse.ArgumentParser(description="Gather intelligence on domains.")
    parser.add_argument("--domain", help="Single domain to analyze")
    parser.add_argument("--input", "-i", help="File with one domain per line")
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(config.OUTPUT_DIR, "intel_results.json"),
        help="Output JSON file"
    )
    args = parser.parse_args()

    domains = []
    if args.domain:
        domains.append(utils.sanitize_domain(args.domain))
    if args.input:
        domains.extend(utils.sanitize_domain(d) for d in utils.read_lines_from_file(args.input))

    domains = utils.deduplicate(domains)
    if not domains:
        print("No domains to analyze. Provide --domain or --input.", file=sys.stderr)
        sys.exit(1)

    results = gather_intel_batch(domains)

    # Save as JSON
    payload = [asdict(r) for r in results]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\n📊 Intel saved to {args.output}")

    # Print summary
    for r in results:
        print(f"\n{'='*60}")
        print(f"Domain:      {r.domain}")
        print(f"IP:          {r.ip} ({r.ip_country}, {r.ip_city})")
        print(f"Title:       {r.page_title[:80]}")
        print(f"Server:      {r.server}")
        print(f"SSL Issuer:  {r.ssl_issuer}")
        print(f"Registrar:   {r.whois_registrar}")
        print(f"Technologies: {r.technologies}")


if __name__ == "__main__":
    main()
