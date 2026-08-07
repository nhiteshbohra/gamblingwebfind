"""
run_pipeline.py — Master CLI Orchestrator & Autonomous Daemon for Gambling Site Discovery & Intelligence Platform.
Supports single-sweep execution or continuous infinite loop daemon mode (--daemon / --continuous).
"""

import argparse
import signal
import sys
import time
from pathlib import Path

import config
from domain_utils import normalize_domain, is_valid_domain
from discover_domains import run_discovery
from classify_domains import run_classification
from resolve_ip import run_ip_resolution
from enrich import run_enrichment
from screenshot import run_screenshots
from keyword_expander import auto_expand_keywords

RUNNING = True


def signal_handler(sig, frame):
    global RUNNING
    print("\n[!] Graceful shutdown signal received. Completing current task and exiting...")
    RUNNING = False


def load_custom_domains(filepath: str) -> list:
    domains = []
    path = Path(filepath)
    if not path.exists():
        print(f"[!] Input domains file not found: {filepath}")
        sys.exit(1)
    
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                norm = normalize_domain(cleaned)
                if norm and is_valid_domain(norm):
                    domains.append(norm)
    return list(dict.fromkeys(domains))


def run_pipeline_cycle(args, cycle_num: int = 1):
    print(f"\n==========================================================================")
    print(f" [*] OSINT Discovery Engine — Cycle #{cycle_num}")
    print(f"==========================================================================")

    # 1. Discovery
    discovered_domains = []
    if args.input_domains:
        print(f"[*] Loading input domains from {args.input_domains}...")
        discovered_domains = load_custom_domains(args.input_domains)
        print(f"[+] Loaded {len(discovered_domains)} custom domains.")
    elif args.all or args.discover:
        discovered_domains = list(
            run_discovery(
                include_dorks=True,
                include_directory_scrape=not args.no_directory_scrape,
                max_pages=args.max_pages,
                max_results=args.max_results
            )
        )

    if not RUNNING:
        return

    # 2. Stage-1 Classification
    if args.all or args.classify:
        run_classification(domains=discovered_domains if args.input_domains else None)

    if not RUNNING:
        return

    # 3. DNS Resolution & Reverse-IP
    if args.all or args.resolve:
        run_ip_resolution()

    if not RUNNING:
        return

    # 4. Deep Intelligence Enrichment
    if args.all or args.enrich:
        run_enrichment()

    if not RUNNING:
        return

    # 5. Playwright Screenshot Capture
    if args.all or args.screenshot:
        run_screenshots()

    # 6. Dynamic Keyword Expansion
    if config.AUTO_EXPAND_KEYWORDS:
        print("[*] Running Dynamic Keyword Auto-Expansion...")
        auto_expand_keywords()

    print("\n==========================================================================")
    print(f" [+] Pipeline Cycle #{cycle_num} Completed Successfully!")
    print(f"  - Discovered Domains CSV : {config.DISCOVERED_CSV}")
    print(f"  - Classified Domains CSV : {config.CLASSIFIED_CSV}")
    print(f"  - IP Mapping CSV         : {config.IP_MAPPING_CSV}")
    print(f"  - Site Intelligence CSV   : {config.SITE_INTEL_CSV}")
    print(f"  - Screenshots Directory  : {config.SCREENSHOTS_DIR}")
    print("==========================================================================")


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(
        description="Gambling Site Discovery & Intelligence Platform — Automated Continuous OSINT Engine"
    )
    
    # Flags for running individual stages or full pipeline
    parser.add_argument("--all", action="store_true", help="Run full pipeline end-to-end")
    parser.add_argument("--daemon", "--continuous", action="store_true", help="Run in continuous daemon mode (never stops)")
    parser.add_argument("--discover", action="store_true", help="Run Stage 1 & 2: Search & Directory Discovery")
    parser.add_argument("--classify", action="store_true", help="Run Stage 3: Homepage Crawl & Classifier")
    parser.add_argument("--resolve", action="store_true", help="Run Stage 4 & 5: DNS & Co-hosted IP Resolution")
    parser.add_argument("--enrich", action="store_true", help="Run Stage 6 & 7: WHOIS, SSL, Tech & Header Enrichment")
    parser.add_argument("--screenshot", action="store_true", help="Run Stage 8: Full-page Playwright Screenshot Capture")

    # Modifiers
    parser.add_argument("--input-domains", type=str, help="Path to custom text file containing seed domains (skips discovery)")
    parser.add_argument("--max-pages", type=int, default=config.MAX_SEARCH_PAGES, help="Max search result pages per query")
    parser.add_argument("--max-results", type=int, default=config.MAX_RESULTS_PER_KEYWORD, help="Max search results per keyword")
    parser.add_argument("--no-directory-scrape", action="store_true", help="Disable directory aggregator web scraping")
    parser.add_argument("--cycle-delay", type=int, default=config.DAEMON_CYCLE_DELAY, help="Delay in seconds between daemon cycles")

    args = parser.parse_args()

    if not any([args.all, args.daemon, args.discover, args.classify, args.resolve, args.enrich, args.screenshot]):
        args.all = True

    if args.daemon:
        cycle_count = 1
        print("==========================================================================")
        print(" [*] AUTONOMOUS DAEMON MODE STARTED — Press Ctrl+C to stop cleanly")
        print("==========================================================================")
        while RUNNING:
            run_pipeline_cycle(args, cycle_num=cycle_count)
            if not RUNNING:
                break
            print(f"\n[*] Cycle #{cycle_count} finished. Waiting {args.cycle_delay} seconds before starting Cycle #{cycle_count + 1}...")
            
            # Sleep in short increments so shutdown signals are handled promptly
            for _ in range(args.cycle_delay):
                if not RUNNING:
                    break
                time.sleep(1)
            
            cycle_count += 1
        print("[!] Continuous Daemon Engine Stopped.")
    else:
        run_pipeline_cycle(args, cycle_num=1)


if __name__ == "__main__":
    main()
