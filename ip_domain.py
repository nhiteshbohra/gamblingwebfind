#!/usr/bin/env python3
"""
reverse_ip.py — Find domains hosted on a given IP address.

Aggregates results from multiple free, no-API-key-required sources:
  - HackerTarget Reverse IP Lookup API
  - RapidDNS.io (sameip search)

Usage:
    python3 reverse_ip.py 8.8.8.8
    python3 reverse_ip.py 8.8.8.8 -o results.txt
    python3 reverse_ip.py 8.8.8.8 --json
    python3 reverse_ip.py --input ips.txt -o results.csv

Notes:
    - No source is authoritative. Shared hosting / CDN IPs (Cloudflare, AWS,
      etc.) can host thousands of domains — no free tool will give you a
      complete list for those. Treat results as a starting point.
    - HackerTarget free tier is rate-limited (~50-100 requests/day per IP).
    - Respect target sites' terms of service and rate limits.
"""

import argparse
import csv
import ipaddress
import json
import re
import sys
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; ReverseIPLookup/1.0; +https://example.com)"
TIMEOUT = 15


@dataclass
class LookupResult:
    ip: str
    domains: set = field(default_factory=set)
    sources_used: dict = field(default_factory=dict)  # source_name -> count
    errors: list = field(default_factory=list)


def validate_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def query_hackertarget(ip: str, session: requests.Session) -> tuple[set, str | None]:
    """Query HackerTarget's free reverse IP lookup API."""
    url = f"https://api.hackertarget.com/reverseiplookup/?q={ip}"
    domains = set()
    try:
        resp = session.get(url, timeout=TIMEOUT)
        text = resp.text.strip()

        if "error" in text.lower() or "API count exceeded" in text:
            return domains, f"HackerTarget: {text}"
        if "No records" in text or "No DNS" in text:
            return domains, None

        for line in text.splitlines():
            line = line.strip()
            if line and not line.lower().startswith("error"):
                domains.add(line.lower())
        return domains, None
    except requests.RequestException as e:
        return domains, f"HackerTarget request failed: {e}"


def query_rapiddns(ip: str, session: requests.Session) -> tuple[set, str | None]:
    """Scrape RapidDNS.io's 'same IP' results page."""
    url = f"https://rapiddns.io/sameip/{ip}?full=1"
    domains = set()
    try:
        resp = session.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return domains, f"RapidDNS: HTTP {resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return domains, None

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue
            candidate = cells[0].get_text(strip=True)
            if candidate and re.match(r"^[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", candidate):
                domains.add(candidate.lower())
        return domains, None
    except requests.RequestException as e:
        return domains, f"RapidDNS request failed: {e}"


SOURCES = {
    "hackertarget": query_hackertarget,
    "rapiddns": query_rapiddns,
}


def lookup(ip: str, session: requests.Session, sources: list[str], delay: float) -> LookupResult:
    result = LookupResult(ip=ip)

    if not validate_ip(ip):
        result.errors.append(f"'{ip}' is not a valid IP address, skipping.")
        return result

    for name in sources:
        func = SOURCES.get(name)
        if not func:
            continue
        found, err = func(ip, session)
        result.sources_used[name] = len(found)
        result.domains |= found
        if err:
            result.errors.append(err)
        time.sleep(delay)

    return result


def print_human(result: LookupResult):
    print(f"\n=== {result.ip} ===")
    if result.errors:
        for e in result.errors:
            print(f"  [!] {e}")
    if not result.domains:
        print("  No domains found.")
        return
    print(f"  {len(result.domains)} domain(s) found:")
    for d in sorted(result.domains):
        print(f"    - {d}")
    print(f"  (per-source counts: {result.sources_used})")


def write_output(results: list[LookupResult], path: str, fmt: str):
    if fmt == "json":
        payload = [
            {
                "ip": r.ip,
                "domains": sorted(r.domains),
                "sources_used": r.sources_used,
                "errors": r.errors,
            }
            for r in results
        ]
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    elif fmt == "csv":
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ip", "domain"])
            for r in results:
                for d in sorted(r.domains):
                    writer.writerow([r.ip, d])
    else:  # plain text
        with open(path, "w") as f:
            for r in results:
                f.write(f"=== {r.ip} ===\n")
                for d in sorted(r.domains):
                    f.write(f"{d}\n")
                f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Find domains hosted on a given IP address (reverse IP lookup)."
    )
    parser.add_argument("ip", nargs="?", help="Single IP address to look up")
    parser.add_argument("--input", "-i", help="File with one IP address per line")
    parser.add_argument("--output", "-o", help="Write results to this file")
    parser.add_argument(
        "--format",
        choices=["txt", "csv", "json"],
        default="txt",
        help="Output file format (default: txt). Ignored if --output not set.",
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON to stdout")
    parser.add_argument(
        "--sources",
        default="hackertarget,rapiddns",
        help="Comma-separated list of sources to use (default: all)",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Delay between source requests in seconds (default: 1.0)"
    )
    args = parser.parse_args()

    if not args.ip and not args.input:
        parser.error("Provide an IP address or --input file.")

    ips = []
    if args.ip:
        ips.append(args.ip)
    if args.input:
        with open(args.input) as f:
            ips.extend(line.strip() for line in f if line.strip())

    sources = [s.strip() for s in args.sources.split(",") if s.strip() in SOURCES]
    if not sources:
        print("No valid sources selected.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results = []
    for ip in ips:
        result = lookup(ip, session, sources, args.delay)
        results.append(result)
        if not args.json:
            print_human(result)

    if args.json:
        print(json.dumps(
            [{"ip": r.ip, "domains": sorted(r.domains), "sources_used": r.sources_used, "errors": r.errors} for r in results],
            indent=2,
        ))

    if args.output:
        write_output(results, args.output, args.format)
        print(f"\nResults written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()