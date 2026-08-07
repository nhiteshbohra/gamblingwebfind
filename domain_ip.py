#!/usr/bin/env python3
"""
domain_to_ip.py — Resolve domain names to their IP address(es).

Usage:
    python3 domain_to_ip.py example.com
    python3 domain_to_ip.py example.com google.com
    python3 domain_to_ip.py --input domains.txt
    python3 domain_to_ip.py --input domains.txt -o results.csv --format csv
    python3 domain_to_ip.py example.com --json

Notes:
    - Resolves both IPv4 (A) and IPv6 (AAAA) records by default.
    - A domain can have multiple IPs (load balancing, CDNs, round-robin DNS) —
      all of them are returned.
    - Uses your system's configured DNS resolver.
"""

import argparse
import csv
import json
import socket
import sys
from dataclasses import dataclass, field


@dataclass
class ResolveResult:
    domain: str
    ipv4: list = field(default_factory=list)
    ipv6: list = field(default_factory=list)
    error: str | None = None


def resolve_domain(domain: str) -> ResolveResult:
    result = ResolveResult(domain=domain)
    try:
        infos = socket.getaddrinfo(domain, None)
        ipv4_set, ipv6_set = set(), set()
        for info in infos:
            family, _, _, _, sockaddr = info
            ip = sockaddr[0]
            if family == socket.AF_INET:
                ipv4_set.add(ip)
            elif family == socket.AF_INET6:
                ipv6_set.add(ip)
        result.ipv4 = sorted(ipv4_set)
        result.ipv6 = sorted(ipv6_set)
        if not result.ipv4 and not result.ipv6:
            result.error = "No A or AAAA records found."
    except socket.gaierror as e:
        result.error = f"Resolution failed: {e}"
    return result


def print_human(result: ResolveResult):
    print(f"\n{result.domain}")
    if result.error:
        print(f"  [!] {result.error}")
        return
    for ip in result.ipv4:
        print(f"  IPv4: {ip}")
    for ip in result.ipv6:
        print(f"  IPv6: {ip}")


def write_output(results: list[ResolveResult], path: str, fmt: str):
    if fmt == "json":
        payload = [
            {"domain": r.domain, "ipv4": r.ipv4, "ipv6": r.ipv6, "error": r.error}
            for r in results
        ]
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    elif fmt == "csv":
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["domain", "ip_type", "ip"])
            for r in results:
                for ip in r.ipv4:
                    writer.writerow([r.domain, "IPv4", ip])
                for ip in r.ipv6:
                    writer.writerow([r.domain, "IPv6", ip])
                if r.error:
                    writer.writerow([r.domain, "ERROR", r.error])
    else:  # txt
        with open(path, "w") as f:
            for r in results:
                f.write(f"{r.domain}\n")
                if r.error:
                    f.write(f"  ERROR: {r.error}\n")
                for ip in r.ipv4:
                    f.write(f"  IPv4: {ip}\n")
                for ip in r.ipv6:
                    f.write(f"  IPv6: {ip}\n")
                f.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Resolve domain names to IP addresses.")
    parser.add_argument("domains", nargs="*", help="One or more domain names")
    parser.add_argument("--input", "-i", help="File with one domain per line")
    parser.add_argument("--output", "-o", help="Write results to this file")
    parser.add_argument(
        "--format", choices=["txt", "csv", "json"], default="txt",
        help="Output file format (default: txt). Ignored if --output not set.",
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON to stdout")
    args = parser.parse_args()

    domains = list(args.domains)
    if args.input:
        with open(args.input) as f:
            domains.extend(line.strip() for line in f if line.strip())

    if not domains:
        parser.error("Provide at least one domain, or use --input file.")

    results = [resolve_domain(d) for d in domains]

    if args.json:
        print(json.dumps(
            [{"domain": r.domain, "ipv4": r.ipv4, "ipv6": r.ipv6, "error": r.error} for r in results],
            indent=2,
        ))
    else:
        for r in results:
            print_human(r)

    if args.output:
        write_output(results, args.output, args.format)
        print(f"\nResults written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()