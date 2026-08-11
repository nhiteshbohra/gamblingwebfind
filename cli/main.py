import os
import sys
import csv
import time
import yaml
import asyncio
import argparse

# Add root project dir to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from storage.db import Database
from storage.csv_exporter import CSVExporter
from orchestrator.scheduler import start_scheduler
from orchestrator.watchdog import start_watchdog

def load_settings(config_path="config/settings.yaml"):
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}

async def async_main():
    parser = argparse.ArgumentParser(description="Presence - Continuous Gambling Domain Discovery Crawler")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("start", help="Start crawler daemon")
    subparsers.add_parser("stats", help="Print crawler stats")

    add_kw = subparsers.add_parser("add-keyword", help="Seed a new keyword")
    add_kw.add_argument("term", type=str, help="Keyword or phrase")

    subparsers.add_parser("export-now", help="Force immediate CSV export")

    dispute_p = subparsers.add_parser("dispute", help="Mark URL ID as disputed")
    dispute_p.add_argument("url_id", type=int, help="URL ID")

    import_p = subparsers.add_parser("import-domains", help="Import domains/URLs from a .txt file")
    import_p.add_argument("file", type=str, help="Path to .txt file, one domain or URL per line")

    vb = subparsers.add_parser("verify-batch", help="Bulk-verify a CSV of pre-existing domains (1M+ capable)")
    vb.add_argument("input", type=str, help="Input CSV path (first column = domain)")
    vb.add_argument("--output", type=str, required=True, help="Output CSV path for strict-verified domains")
    vb.add_argument("--excel-output", type=str, default="output.xlsx", help="Multi-sheet Excel output file (.xlsx)")

    ee = subparsers.add_parser("export-excel", help="Export multi-sheet partitioned Excel workbook")
    ee.add_argument("--output", type=str, default="output.xlsx", help="Excel output file path (.xlsx)")

    ep = subparsers.add_parser("enrich-pending", help="Enrich verified sites with WHOIS & SSL metadata")
    ep.add_argument("--concurrency", type=int, default=3, help="Max parallel WHOIS/SSL queries (default: 3)")
    ep.add_argument("--limit", type=int, default=100, help="Max domains to enrich per run (default: 100)")

    docx_p = subparsers.add_parser("generate-docx-report", help="Generate compact clickable 2-target-per-page Word (.docx) report with screenshots")
    docx_p.add_argument("--input", type=str, default="output.csv", help="Input CSV file or DB source")
    docx_p.add_argument("--output", type=str, default="data/presence_report.docx", help="Output .docx file path")
    docx_p.add_argument("--concurrency", type=int, default=3, help="Screenshot capture concurrency")
    docx_p.add_argument("--limit", type=int, default=50, help="Max targets to include in report")

    args = parser.parse_args()

    settings = load_settings()
    db = Database(db_path=settings.get('db_path', 'data/presence.db'))
    await db.init_db(seed_keywords_file="config/keywords_seed.txt")

    if args.command == "start":
        print("[Presence] Starting discovery crawler...")
        exporter = CSVExporter(db, output_path=settings.get('csv_output_path', 'data/presence_gambling_sites.csv'))

        # Factory: returns a fresh coroutine each call so watchdog can restart the loop
        def scheduler_factory():
            return start_scheduler(db, settings)

        async def periodic_export():
            while True:
                await asyncio.sleep(settings.get('csv_export_interval_minutes', 30) * 60)
                written = await exporter.export()
                print(f"[Exporter] Periodic export completed: {written} rows.")

        # Watchdog owns the scheduler task and restarts it on crash
        await asyncio.gather(
            start_watchdog(db, settings, scheduler_factory=scheduler_factory),
            periodic_export()
        )

    elif args.command == "stats":
        stats = await db.get_stats()
        print("=== Presence Crawler Live Dashboard ===")
        print(f"Active Keywords Pool:       {stats['keywords']}")
        print(f"Discovered in Last 15 Min: {stats['discovered_15m']} URLs")
        print(f"Verified in Last 15 Min:   {stats['verified_15m']} domains")
        print(f"Last CSV Export:            {stats['last_export_at'] or 'Never exported yet'}")
        print("\nURL Status Breakdown:")
        for status, count in stats['urls'].items():
            print(f"  - {status.capitalize()}: {count}")
        
        if stats.get('latest_verified'):
            print("\nRecently Verified Gambling Sites:")
            for item in stats['latest_verified']:
                print(f"  [Verified at {item['verified_at']}] {item['domain']} -> {item['url']}")

    elif args.command == "add-keyword":
        await db.insert_keywords([(args.term.strip(), 'seed', None)])
        print(f"[Presence] Added keyword: '{args.term}'")

    elif args.command == "export-now":
        exporter = CSVExporter(db, output_path=settings.get('csv_output_path', 'data/presence_gambling_sites.csv'))
        count = await exporter.export()
        print(f"[Presence] CSV Exported: {count} verified rows written to {exporter.output_path}")

    elif args.command == "dispute":
        await db.mark_status(args.url_id, 'disputed')
        print(f"[Presence] URL ID {args.url_id} marked as disputed.")

    elif args.command == "import-domains":
        from core.dedup import normalize_url, extract_domain
        if not os.path.exists(args.file):
            print(f"[Presence] File not found: {args.file}")
            return
        with open(args.file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        parsed = []
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith('#'):
                continue
            if not entry.lower().startswith(('http://', 'https://')):
                entry = 'https://' + entry
            norm = normalize_url(entry)
            domain = extract_domain(norm)
            parsed.append((norm, domain))
        result = await db.import_urls(parsed)
        print(f"[Presence] Imported: {result['new']} new, {result['skipped']} already known (skipped)")

    elif args.command == "verify-batch":
        await _run_verify_batch(args, db, settings)

    elif args.command == "export-excel":
        from storage.excel_exporter import ExcelExporter
        excel_path = getattr(args, 'output', 'output.xlsx')
        exporter = ExcelExporter(db_path=settings.get('db_path', 'data/presence.db'), output_path=excel_path)
        stats = exporter.export()
        print(f"[Presence] Multi-sheet Excel exported to {stats['file']}: "
              f"Verified={stats['verified']}, Rejected={stats['rejected']}, "
              f"Dead={stats['dead']}, Blocked={stats['blocked']}")

    elif args.command == "enrich-pending":
        from core.enrich import enrich_pending_verified
        count = await enrich_pending_verified(db, concurrency=args.concurrency, limit=args.limit)
        print(f"[Presence] Intelligence Enrichment: enriched {count} verified domain(s).")

    elif args.command == "generate-docx-report":
        await _run_generate_docx_report(args, db, settings)

    else:
        parser.print_help()


async def _run_generate_docx_report(args, db, settings):
    from core.screenshot_capturer import ScreenshotCapturer
    from storage.docx_report_generator import DocxReportGenerator
    from tqdm import tqdm

    input_path = args.input
    output_path = args.output
    concurrency = args.concurrency
    limit = args.limit

    urls_to_process = []
    if os.path.exists(input_path):
        with open(input_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i == 0 and row and ('url' in row[0].lower() or 'domain' in row[0].lower()):
                    continue
                if row and row[0].strip():
                    url_val = row[0].strip()
                    if not url_val.lower().startswith(('http://', 'https://')):
                        url_val = 'https://' + url_val
                    urls_to_process.append(url_val)
                if limit and len(urls_to_process) >= limit:
                    break
    else:
        verified_rows = await db.get_verified_live_rows()
        for r in verified_rows[:limit]:
            urls_to_process.append(r['url'])

    if not urls_to_process:
        print(f"[DocxReport] No targets found to report.")
        return

    print(f"[DocxReport] Capturing screenshots and building report for {len(urls_to_process)} target(s)...")
    capturer = ScreenshotCapturer(viewport_width=1280, viewport_height=720, quality=68, timeout_seconds=10)
    report = DocxReportGenerator(output_path=output_path)
    pbar = tqdm(total=len(urls_to_process), desc="Docx Report", unit="site")

    items = [(i + 1, url) for i, url in enumerate(urls_to_process)]
    results = await capturer.capture_urls_batch(items, concurrency=concurrency, on_progress=lambda: pbar.update(1))
    pbar.close()

    for idx, url, img_buf in results:
        report.add_target(url=url, image_buffer=img_buf, index=idx)

    saved = report.save()
    print(f"[DocxReport] Word document successfully created: {saved} ({len(results)} targets included).")


async def _run_verify_batch(args, db, settings):
    """One-off bulk verification against a static CSV of pre-existing domains.

    Three outcomes per domain: dead / strict-verified / rejected.
    Resumable: skips rows already processed with verification_tier='strict'.
    Runs standalone — not through the scheduler.
    """
    from core.dedup import normalize_url, extract_domain
    from core.fetcher import fetch
    from core.classifier import classify_strict
    from tqdm import tqdm

    threshold = settings.get('strict_classification_threshold', 0.75)
    concurrency = settings.get('strict_batch_concurrency', 300)
    timeout = settings.get('fetch_timeout_seconds', 6)
    per_domain_delay = settings.get('per_domain_delay_seconds', 0.0)

    # Read CSV: utf-8-sig strips BOM, row 0 skipped unconditionally as header
    domains = []
    with open(args.input, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # always skip header row, regardless of content
            # Take first non-empty column value only
            domain_val = next((c.strip() for c in row if c.strip()), None)
            if domain_val:
                domains.append(domain_val)

    total = len(domains)
    print(f"[verify-batch] Loaded {total} domains from {args.input}")
    print(f"[verify-batch] Concurrency={concurrency}, threshold={threshold}")

    print(f"[verify-batch] Loading settled domains into memory...")
    settled_urls = await db.get_settled_urls_set()
    print(f"[verify-batch] Loaded {len(settled_urls)} pre-settled domains into memory.")

    # Output CSV: open in append mode so Ctrl-C + resume doesn't lose rows
    out_path = args.output
    write_header = not os.path.exists(out_path) or os.path.getsize(out_path) == 0
    out_f = open(out_path, 'a', newline='', encoding='utf-8')
    writer = csv.writer(out_f)
    if write_header:
        writer.writerow(['url', 'domain', 'confidence_score', 'matched_signals', 'checked_at'])
    counts = {'dead': 0, 'verified': 0, 'rejected': 0, 'blocked': 0, 'skipped': 0}
    processed = 0
    start_time = time.monotonic()
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()  # protects CSV writer + counts + processed

    pbar = tqdm(total=total, desc="Verify Batch", unit="dom", dynamic_ncols=True)

    async def handle_domain(raw_domain):
        nonlocal processed
        if not raw_domain.lower().startswith(('http://', 'https://')):
            raw_domain = 'https://' + raw_domain
        url = normalize_url(raw_domain)
        domain = extract_domain(url)

        # Resumability gate: nanosecond memory set check (0 SQL overhead)
        if url in settled_urls:
            async with lock:
                counts['skipped'] += 1
                processed += 1
                pbar.update(1)
                pbar.set_postfix(ver=counts['verified'], rej=counts['rejected'], dead=counts['dead'], blk=counts['blocked'], skip=counts['skipped'], refresh=False)
            return

        # Fast retry loop for connection_failed: up to 2 attempts, 1s between
        result = None
        for slow_attempt in range(2):
            result = await fetch(url, domain, db, timeout_seconds=timeout,
                                 per_domain_delay=per_domain_delay, retries=1)
            if result.html or result.failure_type != 'connection_failed':
                break
            if slow_attempt < 1:
                await asyncio.sleep(1)

        # Route by failure_type
        if result.html:
            # Successful fetch — run classifier
            is_gambling, score, reasons = classify_strict(result.html, url=url, threshold=threshold)
            if is_gambling:
                await db.upsert_strict_result(url, domain, 'verified', score, reasons)
                async with lock:
                    counts['verified'] += 1
                    processed += 1
                    checked_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                    writer.writerow([url, domain, score, '|'.join(reasons), checked_at])
                    out_f.flush()
                    pbar.update(1)
                    pbar.set_postfix(ver=counts['verified'], rej=counts['rejected'], dead=counts['dead'], blk=counts['blocked'], skip=counts['skipped'], refresh=False)
            else:
                await db.upsert_strict_result(url, domain, 'rejected', score, reasons)
                async with lock:
                    counts['rejected'] += 1
                    processed += 1
                    pbar.update(1)
                    pbar.set_postfix(ver=counts['verified'], rej=counts['rejected'], dead=counts['dead'], blk=counts['blocked'], skip=counts['skipped'], refresh=False)
        elif result.failure_type == 'blocked':
            # Bot-blocked: store as 'blocked', not dead — eligible for future re-run
            await db.upsert_strict_result(url, domain, 'blocked')
            async with lock:
                counts['blocked'] += 1
                processed += 1
                pbar.update(1)
                pbar.set_postfix(ver=counts['verified'], rej=counts['rejected'], dead=counts['dead'], blk=counts['blocked'], skip=counts['skipped'], refresh=False)
        else:
            # dead_confirmed or connection_failed after all retries → dead
            await db.upsert_strict_result(url, domain, 'dead')
            async with lock:
                counts['dead'] += 1
                processed += 1
                pbar.update(1)
                pbar.set_postfix(ver=counts['verified'], rej=counts['rejected'], dead=counts['dead'], blk=counts['blocked'], skip=counts['skipped'], refresh=False)

    async def bounded(raw_domain):
        async with sem:
            await handle_domain(raw_domain)

    await asyncio.gather(*[bounded(d) for d in domains])
    pbar.close()
    out_f.close()

    elapsed = time.monotonic() - start_time
    print(f"\n[verify-batch] Done in {elapsed:.0f}s | "
          f"dead={counts['dead']} verified={counts['verified']} "
          f"rejected={counts['rejected']} blocked={counts['blocked']} "
          f"skipped(already-done)={counts['skipped']}")
    print(f"[verify-batch] Strict-verified results written to: {out_path}")

    # Auto-export partitioned multi-sheet Excel workbook (.xlsx)
    try:
        from storage.excel_exporter import ExcelExporter
        excel_out = getattr(args, 'excel_output', 'output.xlsx')
        exporter = ExcelExporter(db_path=settings.get('db_path', 'data/presence.db'), output_path=excel_out)
        stats = exporter.export()
        print(f"[verify-batch] Multi-sheet Excel workbook exported to: {stats['file']} "
              f"(Verified: {stats['verified']}, Rejected: {stats['rejected']}, Dead: {stats['dead']}, Blocked: {stats['blocked']})")
    except Exception as e:
        print(f"[verify-batch] Excel export warning: {e}")



def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[Presence] Stopped by user.")

if __name__ == "__main__":
    main()
