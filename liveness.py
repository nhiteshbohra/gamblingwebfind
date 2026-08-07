#!/usr/bin/env python3
"""
liveness.py — Check whether discovered domains are alive and responding.

Tests both HTTP and HTTPS, records status codes, response times,
redirect chains, and final URLs. Uses concurrent threads for speed.

Usage:
    python liveness.py --input domains.txt
    python liveness.py --input domains.txt -o live_domains.txt --threads 20
    python liveness.py --domain example.com
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests

import config
import utils

logger = utils.setup_logging("liveness")


@dataclass
class LivenessResult:
    domain: str
    is_up: bool = False
    status: str = "DOWN"        # UP, DOWN, REDIRECT, ERROR
    http_code: int = 0
    response_time_ms: int = 0
    final_url: str = ""
    redirect_chain: list = field(default_factory=list)
    error: str = ""
    protocol: str = ""          # http or https


def check_domain(domain: str, timeout: int = config.LIVENESS_TIMEOUT) -> LivenessResult:
    """
    Check if a domain is alive. Tries HTTPS first, then HTTP.
    Returns a LivenessResult with status information.
    """
    result = LivenessResult(domain=domain)
    session = utils.create_session()
    session.max_redirects = 10

    # Try HTTPS first, then HTTP
    for scheme in ["https", "http"]:
        url = f"{scheme}://{domain}"
        try:
            start = time.time()
            resp = session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                verify=False,  # Many gambling sites have bad SSL
            )
            elapsed_ms = int((time.time() - start) * 1000)

            result.is_up = True
            result.http_code = resp.status_code
            result.response_time_ms = elapsed_ms
            result.final_url = resp.url
            result.protocol = scheme
            result.redirect_chain = [r.url for r in resp.history]

            if resp.history:
                result.status = "REDIRECT"
            elif 200 <= resp.status_code < 400:
                result.status = "UP"
            else:
                result.status = f"UP ({resp.status_code})"

            return result

        except requests.exceptions.SSLError:
            if scheme == "https":
                continue  # Fall back to HTTP
            result.error = "SSL error on both protocols"
        except requests.exceptions.ConnectionError:
            if scheme == "https":
                continue
            result.error = "Connection refused"
        except requests.exceptions.Timeout:
            if scheme == "https":
                continue
            result.error = "Timeout"
        except requests.exceptions.TooManyRedirects:
            result.error = "Too many redirects"
            result.status = "ERROR"
            return result
        except requests.exceptions.RequestException as e:
            if scheme == "https":
                continue
            result.error = str(e)

    result.status = "DOWN"
    return result


def check_domains_batch(
    domains: list[str],
    threads: int = config.LIVENESS_THREADS,
    timeout: int = config.LIVENESS_TIMEOUT,
) -> list[LivenessResult]:
    """
    Check liveness for a batch of domains using concurrent threads.
    Returns a list of LivenessResult objects.
    """
    results = []
    total = len(domains)

    logger.info(f"Checking liveness for {total} domain(s) with {threads} threads...")

    with ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_domain = {
            executor.submit(check_domain, domain, timeout): domain
            for domain in domains
        }

        done_count = 0
        for future in as_completed(future_to_domain):
            done_count += 1
            result = future.result()
            results.append(result)

            status_icon = "✅" if result.is_up else "❌"
            logger.info(
                f"  [{done_count}/{total}] {status_icon} {result.domain} "
                f"→ {result.status} ({result.http_code}) [{result.response_time_ms}ms]"
            )

    up_count = sum(1 for r in results if r.is_up)
    logger.info(f"\n📊 Results: {up_count} UP / {total - up_count} DOWN out of {total} domains")
    return results


def get_live_domains(results: list[LivenessResult]) -> list[str]:
    """Extract only the live domain names from results."""
    return [r.domain for r in results if r.is_up]


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    # Suppress InsecureRequestWarning from urllib3 (we set verify=False)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    parser = argparse.ArgumentParser(description="Check domain liveness.")
    parser.add_argument("--domain", help="Single domain to check")
    parser.add_argument("--input", "-i", help="File with one domain per line")
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(config.OUTPUT_DIR, "live_domains.txt"),
        help="Output file for live domains"
    )
    parser.add_argument(
        "--threads", type=int, default=config.LIVENESS_THREADS,
        help=f"Number of concurrent threads (default: {config.LIVENESS_THREADS})"
    )
    parser.add_argument(
        "--timeout", type=int, default=config.LIVENESS_TIMEOUT,
        help=f"Timeout per domain in seconds (default: {config.LIVENESS_TIMEOUT})"
    )
    args = parser.parse_args()

    domains = []
    if args.domain:
        domains.append(utils.sanitize_domain(args.domain))
    if args.input:
        domains.extend(utils.sanitize_domain(d) for d in utils.read_lines_from_file(args.input))

    domains = utils.deduplicate(domains)
    if not domains:
        print("No domains to check. Provide --domain or --input.", file=sys.stderr)
        sys.exit(1)

    results = check_domains_batch(domains, threads=args.threads, timeout=args.timeout)
    live = get_live_domains(results)

    if live:
        with open(args.output, "w", encoding="utf-8") as f:
            for d in sorted(live):
                f.write(d + "\n")
        print(f"\n🎯 {len(live)} live domain(s) saved to {args.output}")
    else:
        print("\n⚠️  No live domains found.")


if __name__ == "__main__":
    main()
