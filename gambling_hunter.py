#!/usr/bin/env python3
"""
gambling_hunter.py — Main orchestrator for the Gambling Website Detection
& Intelligence Automation Tool.

Chains all phases into a single pipeline:
  Dork → Discover → IP Resolution → Reverse IP → Liveness → Intel → Screenshot → CSV

Usage:
    # Full auto: dork → discover → enumerate → check → gather → screenshot → CSV
    python gambling_hunter.py --auto

    # From existing domain list
    python gambling_hunter.py --domains domains.txt

    # From existing IP list
    python gambling_hunter.py --ips iplist.txt

    # From forensic PDF
    python gambling_hunter.py --pdf full_betting.pdf

    # Custom dork queries
    python gambling_hunter.py --dork "intitle:'cricket betting' site:.in"
    python gambling_hunter.py --dork-file my_dorks.txt

    # Control options
    python gambling_hunter.py --auto --threads 20 --no-screenshot --delay 5
    python gambling_hunter.py --ips iplist.txt --max-domains 100

    # Skip specific phases
    python gambling_hunter.py --domains domains.txt --skip-reverse-ip
    python gambling_hunter.py --auto --no-screenshot --skip-whois
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime

import config
import utils

# Import pipeline modules
import dorker
import liveness
import intel
import screenshotter

# Conditional imports for existing scripts
from ip_domain import lookup as reverse_ip_lookup, SOURCES as REVERSE_IP_SOURCES
from domain_ip import resolve_domain

logger = utils.setup_logging("gambling_hunter")


def banner():
    """Print the tool banner."""
    print(r"""
╔══════════════════════════════════════════════════════════════╗
║          🎰  GAMBLING WEBSITE HUNTER  🎰                    ║
║    Detection & Intelligence Automation Tool                  ║
║                                                              ║
║    Dork → Discover → Enumerate → Liveness →                  ║
║           Intel → Screenshot → CSV Report                    ║
╚══════════════════════════════════════════════════════════════╝
    """)


def phase_pdf_extract(pdf_path: str) -> tuple[list[str], list[str]]:
    """Phase 0: Extract domains and IPs from a forensic PDF."""
    logger.info("═══ Phase 0: PDF Extraction ═══")

    csv_output = os.path.join(config.OUTPUT_DIR, "pdf_extracted.csv")
    try:
        subprocess.run(
            [sys.executable, "datafrompdf.py", pdf_path, "-o", csv_output],
            check=True,
            cwd=config.BASE_DIR,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"PDF extraction failed: {e}")
        return [], []

    domains = []
    ips = []
    if os.path.exists(csv_output):
        with open(csv_output, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = row.get("target_domain", "").strip()
                ip = row.get("resolved_ip", "").strip()
                if d and utils.is_valid_domain(d):
                    domains.append(d)
                if ip and utils.is_valid_ip(ip):
                    ips.append(ip)

    domains = utils.deduplicate(domains)
    ips = utils.deduplicate(ips)
    logger.info(f"  Extracted {len(domains)} domains and {len(ips)} IPs from PDF")
    return domains, ips


def phase_dorking(args) -> list[str]:
    """Phase 1: Google Dorking to discover gambling websites."""
    logger.info("═══ Phase 1: Google Dorking ═══")

    dork_queries = dorker.load_dorks(
        dork_file=args.dork_file,
        extra_dorks=args.dork if args.dork else None,
    )

    if not dork_queries:
        logger.warning("No dork queries available. Skipping dorking phase.")
        return []

    domains = dorker.run_dorking(
        dork_queries,
        max_results_per_query=args.max_results,
        delay_min=args.delay,
        delay_max=args.delay * 2,
    )

    # Save discovered domains
    if domains:
        output_path = os.path.join(config.OUTPUT_DIR, "discovered_domains.txt")
        dorker.save_domains(domains, output_path)

    return domains


def phase_ip_resolution(domains: list[str]) -> dict[str, str]:
    """Phase 2a: Resolve domains to IP addresses."""
    logger.info("═══ Phase 2a: DNS Resolution ═══")
    domain_to_ip = {}
    total = len(domains)

    for i, domain in enumerate(domains, 1):
        result = resolve_domain(domain)
        ip = result.ipv4[0] if result.ipv4 else ""
        domain_to_ip[domain] = ip
        if ip:
            logger.info(f"  [{i}/{total}] {domain} → {ip}")
        else:
            logger.info(f"  [{i}/{total}] {domain} → FAILED ({result.error})")

    resolved = sum(1 for v in domain_to_ip.values() if v)
    logger.info(f"  Resolved {resolved}/{total} domains")
    return domain_to_ip


def phase_reverse_ip(
    ips: list[str],
    existing_domains: set[str],
    delay: float = config.REVERSE_IP_DELAY,
) -> list[str]:
    """Phase 2b: Reverse IP lookup to find additional domains on same IPs."""
    logger.info("═══ Phase 2b: Reverse IP Lookup ═══")

    import requests as req_lib
    session = req_lib.Session()
    session.headers.update({"User-Agent": utils.get_random_user_agent()})

    new_domains = set()
    unique_ips = utils.deduplicate(ips)
    total = len(unique_ips)
    sources = list(REVERSE_IP_SOURCES.keys())

    for i, ip in enumerate(unique_ips, 1):
        logger.info(f"  [{i}/{total}] Reverse lookup: {ip}")
        result = reverse_ip_lookup(ip, session, sources, delay)

        for d in result.domains:
            if d not in existing_domains:
                new_domains.add(d)

        if result.errors:
            for e in result.errors:
                logger.debug(f"    Error: {e}")

        if i < total:
            utils.rate_sleep(delay)

    logger.info(f"  Found {len(new_domains)} NEW domains via reverse IP lookup")
    return sorted(new_domains)


def phase_liveness(domains: list[str], threads: int) -> tuple[list[str], dict]:
    """Phase 3: Check which domains are alive."""
    logger.info("═══ Phase 3: Liveness Check ═══")

    results = liveness.check_domains_batch(domains, threads=threads)
    live = liveness.get_live_domains(results)

    # Build a lookup dict for status info
    liveness_data = {}
    for r in results:
        liveness_data[r.domain] = {
            "status": r.status,
            "http_code": r.http_code,
            "response_time_ms": r.response_time_ms,
            "final_url": r.final_url,
        }

    # Save live domains
    live_path = os.path.join(config.OUTPUT_DIR, "live_domains.txt")
    with open(live_path, "w", encoding="utf-8") as f:
        for d in sorted(live):
            f.write(d + "\n")

    logger.info(f"  {len(live)} live domains saved to {live_path}")
    return live, liveness_data


def phase_intel(domains: list[str]) -> dict[str, intel.DomainIntel]:
    """Phase 4: Gather intelligence on live domains."""
    logger.info("═══ Phase 4: Intelligence Gathering ═══")

    results = intel.gather_intel_batch(domains)
    intel_data = {r.domain: r for r in results}
    return intel_data


def phase_screenshots(
    domains: list[str],
) -> dict[str, str]:
    """Phase 5: Capture homepage screenshots."""
    logger.info("═══ Phase 5: Screenshot Capture ═══")

    results = screenshotter.capture_screenshots_batch(domains)
    return results


def phase_csv_export(
    all_domains: list[str],
    domain_to_ip: dict[str, str],
    reverse_ip_counts: dict[str, int],
    reverse_ip_domains: dict[str, str],
    liveness_data: dict,
    intel_data: dict,
    screenshot_paths: dict[str, str],
    discovery_sources: dict[str, str],
    output_path: str,
):
    """Phase 6: Export everything to a CSV report."""
    logger.info("═══ Phase 6: CSV Export ═══")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=config.CSV_COLUMNS)
        writer.writeheader()

        for domain in sorted(all_domains):
            live_info = liveness_data.get(domain, {})
            intel_info = intel_data.get(domain, None)
            ip = domain_to_ip.get(domain, "")

            row = {
                "domain": domain,
                "ip": ip or (intel_info.ip if intel_info else ""),
                "reverse_ip_domain_count": reverse_ip_counts.get(ip, 0),
                "reverse_ip_domains": reverse_ip_domains.get(ip, ""),
                "status": live_info.get("status", "UNKNOWN"),
                "http_code": live_info.get("http_code", ""),
                "response_time_ms": live_info.get("response_time_ms", ""),
                "final_url": live_info.get("final_url", ""),
                "page_title": intel_info.page_title if intel_info else "",
                "meta_description": intel_info.meta_description if intel_info else "",
                "meta_keywords": intel_info.meta_keywords if intel_info else "",
                "content_language": intel_info.content_language if intel_info else "",
                "server": intel_info.server if intel_info else "",
                "technologies": intel_info.technologies if intel_info else "",
                "ssl_issuer": intel_info.ssl_issuer if intel_info else "",
                "ssl_expiry": intel_info.ssl_expiry if intel_info else "",
                "whois_registrar": intel_info.whois_registrar if intel_info else "",
                "whois_created": intel_info.whois_created if intel_info else "",
                "whois_expires": intel_info.whois_expires if intel_info else "",
                "whois_country": intel_info.whois_country if intel_info else "",
                "ip_country": intel_info.ip_country if intel_info else "",
                "ip_city": intel_info.ip_city if intel_info else "",
                "ip_isp": intel_info.ip_isp if intel_info else "",
                "screenshot_path": screenshot_paths.get(domain, ""),
                "discovery_source": discovery_sources.get(domain, "manual"),
                "scan_timestamp": timestamp,
            }
            writer.writerow(row)

    logger.info(f"  📄 CSV report saved: {output_path}")
    logger.info(f"  📊 Total domains in report: {len(all_domains)}")


def run_pipeline(args):
    """Execute the full pipeline based on CLI arguments."""
    banner()
    start_time = time.time()

    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ─── Collect input domains and IPs ────────────────────────────────────
    all_domains = set()
    all_ips = set()
    discovery_sources = {}  # domain → source label

    # Source: PDF extraction
    if args.pdf:
        pdf_domains, pdf_ips = phase_pdf_extract(args.pdf)
        for d in pdf_domains:
            all_domains.add(d)
            discovery_sources[d] = "pdf"
        all_ips.update(pdf_ips)

    # Source: Google Dorking
    if args.auto or args.dork or args.dork_file:
        dork_domains = phase_dorking(args)
        for d in dork_domains:
            all_domains.add(d)
            discovery_sources[d] = "dorking"

    # Source: Domain list file
    if args.domains:
        file_domains = [utils.sanitize_domain(d) for d in utils.read_lines_from_file(args.domains)]
        file_domains = [d for d in file_domains if utils.is_valid_domain(d)]
        for d in file_domains:
            all_domains.add(d)
            discovery_sources[d] = "file"
        logger.info(f"Loaded {len(file_domains)} domains from {args.domains}")

    # Source: IP list file
    if args.ips:
        file_ips = [ip.strip() for ip in utils.read_lines_from_file(args.ips)]
        file_ips = [ip for ip in file_ips if utils.is_valid_ip(ip)]
        all_ips.update(file_ips)
        logger.info(f"Loaded {len(file_ips)} IPs from {args.ips}")

    # ─── Phase 2a: Resolve domains to IPs ────────────────────────────────
    domain_to_ip = {}
    if all_domains:
        domain_to_ip = phase_ip_resolution(list(all_domains))
        # Add resolved IPs to the IP pool
        for ip in domain_to_ip.values():
            if ip:
                all_ips.add(ip)

    # ─── Phase 2b: Reverse IP lookup ─────────────────────────────────────
    reverse_ip_counts = {}
    reverse_ip_domains_str = {}

    if not args.skip_reverse_ip and all_ips:
        ip_list = utils.deduplicate(list(all_ips))

        # Apply max-domains limit if set (limit number of IPs to process)
        if args.max_reverse_ips and len(ip_list) > args.max_reverse_ips:
            logger.info(f"Limiting reverse IP lookups to {args.max_reverse_ips} IPs (out of {len(ip_list)})")
            ip_list = ip_list[:args.max_reverse_ips]

        new_domains = phase_reverse_ip(ip_list, all_domains, delay=args.delay)

        # Track reverse IP results
        import requests as req_lib
        session = req_lib.Session()
        session.headers.update({"User-Agent": utils.get_random_user_agent()})
        sources = list(REVERSE_IP_SOURCES.keys())

        for ip in ip_list:
            result = reverse_ip_lookup(ip, session, sources, 0.5)
            count = len(result.domains)
            reverse_ip_counts[ip] = count
            reverse_ip_domains_str[ip] = "; ".join(sorted(result.domains)[:20])  # limit to 20 for CSV

        for d in new_domains:
            all_domains.add(d)
            discovery_sources[d] = "reverse_ip"

    # ─── Phase 3: Liveness check ──────────────────────────────────────────
    all_domains_list = sorted(all_domains)

    # Apply max-domains limit
    if args.max_domains and len(all_domains_list) > args.max_domains:
        logger.info(f"Limiting scan to {args.max_domains} domains (out of {len(all_domains_list)})")
        all_domains_list = all_domains_list[:args.max_domains]

    live_domains, liveness_data = phase_liveness(all_domains_list, threads=args.threads)

    if not live_domains:
        logger.warning("No live domains found. Generating report with available data.")
        # Still export CSV with what we have
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(config.OUTPUT_DIR, f"gambling_report_{timestamp}.csv")
        phase_csv_export(
            all_domains_list, domain_to_ip, reverse_ip_counts,
            reverse_ip_domains_str, liveness_data, {},
            {}, discovery_sources, csv_path,
        )
        return

    # ─── Phase 4: Intelligence gathering ──────────────────────────────────
    intel_data = phase_intel(live_domains)

    # Update domain_to_ip with intel-resolved IPs
    for d, info in intel_data.items():
        if info.ip and d not in domain_to_ip:
            domain_to_ip[d] = info.ip

    # ─── Phase 5: Screenshots ────────────────────────────────────────────
    screenshot_paths = {}
    if not args.no_screenshot:
        screenshot_paths = phase_screenshots(live_domains)
    else:
        logger.info("═══ Phase 5: Screenshots SKIPPED (--no-screenshot) ═══")

    # ─── Phase 6: CSV Export ──────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(config.OUTPUT_DIR, f"gambling_report_{timestamp}.csv")

    phase_csv_export(
        all_domains_list,
        domain_to_ip,
        reverse_ip_counts,
        reverse_ip_domains_str,
        liveness_data,
        intel_data,
        screenshot_paths,
        discovery_sources,
        csv_path,
    )

    # ─── Summary ──────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    🏁  SCAN COMPLETE  🏁                     ║
╠══════════════════════════════════════════════════════════════╣
║  Total Domains Scanned:  {len(all_domains_list):<35}║
║  Live Domains:           {len(live_domains):<35}║
║  Screenshots Captured:   {sum(1 for v in screenshot_paths.values() if v):<35}║
║  CSV Report:             {os.path.basename(csv_path):<35}║
║  Time Elapsed:           {elapsed:.1f}s{' '*(34-len(f'{elapsed:.1f}s'))}║
╠══════════════════════════════════════════════════════════════╣
║  Output Directory: {config.OUTPUT_DIR:<40}║
╚══════════════════════════════════════════════════════════════╝
    """)


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="🎰 Gambling Website Hunter — Detection & Intelligence Automation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python gambling_hunter.py --auto                         Full auto pipeline
  python gambling_hunter.py --domains domains.txt          From domain list
  python gambling_hunter.py --ips iplist.txt               From IP list
  python gambling_hunter.py --pdf report.pdf               From forensic PDF
  python gambling_hunter.py --dork "intitle:'online casino'"  Custom dork
  python gambling_hunter.py --auto --no-screenshot         Skip screenshots
        """,
    )

    # ─── Input Sources ────────────────────────────────────────────────────
    input_group = parser.add_argument_group("Input Sources")
    input_group.add_argument(
        "--auto", action="store_true",
        help="Full auto mode: run dorking with built-in queries + all phases"
    )
    input_group.add_argument("--domains", help="File with domain names (one per line)")
    input_group.add_argument("--ips", help="File with IP addresses (one per line)")
    input_group.add_argument("--pdf", help="Forensic PDF to extract domains/IPs from")
    input_group.add_argument(
        "--dork", action="append", default=[],
        help="Custom dork query (can be used multiple times)"
    )
    input_group.add_argument("--dork-file", help="File with dork queries (one per line)")

    # ─── Control Options ──────────────────────────────────────────────────
    control_group = parser.add_argument_group("Control Options")
    control_group.add_argument(
        "--max-results", type=int, default=config.MAX_DORK_RESULTS_PER_QUERY,
        help=f"Max results per dork query (default: {config.MAX_DORK_RESULTS_PER_QUERY})"
    )
    control_group.add_argument(
        "--max-domains", type=int, default=None,
        help="Max total domains to process (default: unlimited)"
    )
    control_group.add_argument(
        "--max-reverse-ips", type=int, default=None,
        help="Max IPs for reverse lookup (default: unlimited)"
    )
    control_group.add_argument(
        "--threads", type=int, default=config.LIVENESS_THREADS,
        help=f"Concurrent threads for liveness checks (default: {config.LIVENESS_THREADS})"
    )
    control_group.add_argument(
        "--delay", type=float, default=config.REQUEST_DELAY,
        help=f"Delay between requests in seconds (default: {config.REQUEST_DELAY})"
    )

    # ─── Skip Options ────────────────────────────────────────────────────
    skip_group = parser.add_argument_group("Skip Options")
    skip_group.add_argument(
        "--no-screenshot", action="store_true",
        help="Skip screenshot capture phase"
    )
    skip_group.add_argument(
        "--skip-reverse-ip", action="store_true",
        help="Skip reverse IP lookup phase"
    )

    # ─── Output ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--output-dir", default=config.OUTPUT_DIR,
        help=f"Output directory (default: {config.OUTPUT_DIR})"
    )

    args = parser.parse_args()

    # Validate: at least one input source required
    if not (args.auto or args.domains or args.ips or args.pdf or args.dork or args.dork_file):
        parser.error(
            "At least one input source required: --auto, --domains, --ips, --pdf, --dork, or --dork-file"
        )

    # Override output dir if specified
    if args.output_dir != config.OUTPUT_DIR:
        config.OUTPUT_DIR = args.output_dir
        config.SCREENSHOT_DIR = os.path.join(args.output_dir, "screenshots")
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)

    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        print("\n\n⚠️  Scan interrupted by user. Partial results may be in the output directory.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
