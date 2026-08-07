#!/usr/bin/env python3
"""
dorker.py — Google Dorking engine for autonomous gambling website discovery.

Searches Google with gambling/betting-specific dork queries and extracts
discovered domains.

Usage:
    python dorker.py                                     # Use built-in dorks
    python dorker.py --dork "intitle:'online betting'"   # Single custom dork
    python dorker.py --dork-file my_dorks.txt            # Dorks from file
    python dorker.py --max-results 20 --delay 15         # Control rate/count
"""

import argparse
import os
import random
import sys
import time

try:
    from googlesearch import search as google_search  # type: ignore[import-not-found]
except ImportError:
    google_search = None

import config
import utils

logger = utils.setup_logging("dorker")

# ─── Default dork file ───────────────────────────────────────────────────────
DEFAULT_DORKS_FILE = os.path.join(config.BASE_DIR, "dorks.txt")


def load_dorks(dork_file: str | None = None, extra_dorks: list[str] | None = None) -> list[str]:
    """Load dork queries from file and/or extra CLI arguments."""
    dorks = []

    # Load from file
    source = dork_file or DEFAULT_DORKS_FILE
    if os.path.exists(source):
        file_dorks = utils.read_lines_from_file(source)
        # Filter out comments
        file_dorks = [d for d in file_dorks if not d.startswith("#")]
        dorks.extend(file_dorks)
        logger.info(f"Loaded {len(file_dorks)} dork queries from {source}")

    # Add extra dorks from CLI
    if extra_dorks:
        dorks.extend(extra_dorks)

    if not dorks:
        logger.warning("No dork queries available. Provide --dork or --dork-file.")
    
    return dorks


def search_google(query: str, max_results: int = 50, delay: float = 10.0) -> list[str]:
    """
    Search Google with a dork query and return discovered URLs.
    Uses the googlesearch-python library.
    """
    if google_search is None:
        logger.error(
            "googlesearch-python is not installed. Run: pip install googlesearch-python"
        )
        return []

    urls = []
    try:
        logger.info(f"Dorking: {query}")
        results = google_search(
            query,
            num_results=max_results,
            sleep_interval=int(delay),
            lang="en",
        )
        for url in results:
            urls.append(url)
        logger.info(f"  → Found {len(urls)} result(s)")
    except Exception as e:
        logger.error(f"Google search failed for '{query}': {e}")

    return urls


def extract_domains_from_urls(urls: list[str]) -> list[str]:
    """Extract unique domain names from a list of URLs, filtering noise."""
    domains = set()
    for url in urls:
        domain = utils.extract_domain(url)
        if not domain:
            continue
        # Skip excluded domains (social media, news, big tech, etc.)
        if domain in config.DORK_EXCLUDE_DOMAINS:
            continue
        # Skip if domain is a subdomain of an excluded domain
        skip = False
        for excluded in config.DORK_EXCLUDE_DOMAINS:
            if domain.endswith("." + excluded):
                skip = True
                break
        if skip:
            continue
        if utils.is_valid_domain(domain):
            domains.add(domain)
    return sorted(domains)


def run_dorking(
    dorks: list[str],
    max_results_per_query: int = config.MAX_DORK_RESULTS_PER_QUERY,
    delay_min: float = config.DORK_DELAY_MIN,
    delay_max: float = config.DORK_DELAY_MAX,
) -> list[str]:
    """
    Execute all dork queries and return a de-duplicated list of discovered domains.
    """
    all_urls = []
    total = len(dorks)

    for i, dork in enumerate(dorks, 1):
        logger.info(f"[{i}/{total}] Running dork query...")
        urls = search_google(dork, max_results=max_results_per_query, delay=5)
        all_urls.extend(urls)

        if i < total:
            wait = random.uniform(delay_min, delay_max)
            logger.info(f"  Waiting {wait:.1f}s before next query...")
            time.sleep(wait)

    domains = extract_domains_from_urls(all_urls)
    logger.info(f"\n✅ Dorking complete: {len(domains)} unique domain(s) discovered from {len(dorks)} queries.")
    return domains


def save_domains(domains: list[str], output_path: str):
    """Save discovered domains to a text file."""
    with open(output_path, "w", encoding="utf-8") as f:
        for d in domains:
            f.write(d + "\n")
    logger.info(f"Saved {len(domains)} domains to {output_path}")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Google Dorking engine for gambling website discovery."
    )
    parser.add_argument(
        "--dork", action="append", default=[],
        help="Custom dork query (can be used multiple times)"
    )
    parser.add_argument(
        "--dork-file", default=None,
        help="Path to a file with dork queries (one per line)"
    )
    parser.add_argument(
        "--max-results", type=int, default=config.MAX_DORK_RESULTS_PER_QUERY,
        help=f"Max results per dork query (default: {config.MAX_DORK_RESULTS_PER_QUERY})"
    )
    parser.add_argument(
        "--delay", type=float, default=config.DORK_DELAY_MIN,
        help=f"Min delay between queries in seconds (default: {config.DORK_DELAY_MIN})"
    )
    parser.add_argument(
        "-o", "--output", default=os.path.join(config.OUTPUT_DIR, "discovered_domains.txt"),
        help="Output file path"
    )
    args = parser.parse_args()

    dorks = load_dorks(dork_file=args.dork_file, extra_dorks=args.dork if args.dork else None)
    if not dorks:
        print("No dork queries to run. Provide --dork or --dork-file.", file=sys.stderr)
        sys.exit(1)

    domains = run_dorking(dorks, max_results_per_query=args.max_results, delay_min=args.delay)

    if domains:
        save_domains(domains, args.output)
        print(f"\n🎯 {len(domains)} gambling-related domains discovered.")
    else:
        print("\n⚠️  No domains discovered. Try different dork queries.")


if __name__ == "__main__":
    main()
