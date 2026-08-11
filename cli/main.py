import os
import sys
import csv
import time
import yaml
import asyncio
import argparse
from datetime import datetime

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

    args = parser.parse_args()

    settings = load_settings()
    db = Database(db_path=settings.get('db_path', 'data/presence.db'))
    await db.init_db(seed_keywords_file="config/keywords_seed.txt")

    if args.command == "start":
        print("[Presence] Starting discovery crawler...")
        exporter = CSVExporter(db, output_path=settings.get('csv_output_path', 'data/presence_gambling_sites.csv'))

        def scheduler_factory():
            return start_scheduler(db, settings)

        async def periodic_export():
            while True:
                await asyncio.sleep(settings.get('csv_export_interval_minutes', 30) * 60)
                try:
                    count = await exporter.export()
                    print(f"[Presence] Periodic CSV export complete: {count} verified rows.")
                except Exception as e:
                    print(f"[Presence] Periodic CSV export error: {e}")

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
        await db.record_classification(status='disputed', url_id=args.url_id)
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

    else:
        parser.print_help()


async def _run_verify_batch(args, db, settings):
    from core.dedup import normalize_url, extract_domain
    from core.fetcher import fetch
    from core.classifier import classify_strict
    from tqdm import tqdm

    threshold = settings.get('strict_classification_threshold', 0.75)
    concurrency = settings.get('strict_batch_concurrency', 300)
    per_domain_delay = settings.get('strict_batch_per_domain_delay', 0.0)
    db_batch_size = 500
    csv_input_path = args.input
    csv_output_path = args.output

    if not os.path.exists(csv_input_path):
        print(f"[verify-batch] Error: input file '{csv_input_path}' not found.")
        return

    print(f"[verify-batch] Pre-loading settled URLs into memory...")
    settled_urls = await db.get_settled_urls_set(tier='strict')
    print(f"[verify-batch] Loaded {len(settled_urls):,} settled URLs into memory set.")

    raw_candidates = []
    with open(csv_input_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0 and row and ('url' in row[0].lower() or 'domain' in row[0].lower()):
                continue
            if row and row[0].strip():
                url_val = row[0].strip()
                if not url_val.lower().startswith(('http://', 'https://')):
                    url_val = 'https://' + url_val
                raw_candidates.append(url_val)

    total_candidates = len(raw_candidates)

    stats = {'dead': 0, 'verified': 0, 'rejected': 0, 'blocked': 0, 'skipped': 0}
    db_buffer = []

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_candidates, desc="Verify Batch", unit="dom")
    start_time = time.time()

    csv_out_file = open(csv_output_path, 'a', encoding='utf-8', newline='')
    csv_writer = csv.writer(csv_out_file)

    async def flush_buffer():
        nonlocal db_buffer
        if not db_buffer:
            return
        for norm_url, domain_name, c_status, score, reasons_str, _, _ in db_buffer:
            await db.record_classification(norm_url, domain_name, c_status, confidence_score=score, reasons=reasons_str, tier='strict')
        db_buffer.clear()

    async def process_one(raw_url: str):
        norm_url = normalize_url(raw_url)
        domain_name = extract_domain(norm_url)

        if norm_url in settled_urls:
            stats['skipped'] += 1
            pbar.update(1)
            return

        async with sem:
            res = await fetch(norm_url, domain_name, db, timeout_seconds=6)
            status = res.status_code or 0

            if status in (403, 429, 503) or res.failure_type == 'blocked':
                db_buffer.append((norm_url, domain_name, 'blocked', 0.0, f"HTTP {status}", None, None))
                stats['blocked'] += 1
            elif status == 0 or res.error or res.failure_type == 'dead_confirmed' or not res.html:
                db_buffer.append((norm_url, domain_name, 'dead', 0.0, res.error or 'Network error', None, None))
                stats['dead'] += 1
            else:
                is_gambling, score_val, reasons_list = classify_strict(html=res.html, url=norm_url, threshold=threshold)
                c_status = 'verified' if is_gambling else 'rejected'
                reasons_str = "|".join(reasons_list) if reasons_list else ''

                db_buffer.append((norm_url, domain_name, c_status, score_val, reasons_str, None, None))

                if is_gambling:
                    stats['verified'] += 1
                    csv_writer.writerow([norm_url, domain_name, score_val, reasons_str, datetime.utcnow().isoformat() + 'Z'])
                    csv_out_file.flush()
                else:
                    stats['rejected'] += 1

            if len(db_buffer) >= db_batch_size:
                await flush_buffer()

            pbar.update(1)
            pbar.set_postfix(
                ver=stats['verified'],
                rej=stats['rejected'],
                dead=stats['dead'],
                blk=stats['blocked'],
                skip=stats['skipped']
            )

            if per_domain_delay > 0:
                await asyncio.sleep(per_domain_delay)

    tasks = [process_one(u) for u in raw_candidates]
    await asyncio.gather(*tasks)
    await flush_buffer()

    csv_out_file.close()
    pbar.close()

    elapsed = int(time.time() - start_time)
    print(f"\n[verify-batch] Done in {elapsed}s | dead={stats['dead']} verified={stats['verified']} "
          f"rejected={stats['rejected']} blocked={stats['blocked']} skipped(already-done)={stats['skipped']}")
    print(f"[verify-batch] Strict-verified results written to: {csv_output_path}")

    from storage.excel_exporter import ExcelExporter
    excel_path = getattr(args, 'excel_output', 'output.xlsx')
    exporter = ExcelExporter(db_path=settings.get('db_path', 'data/presence.db'), output_path=excel_path)
    ex_stats = exporter.export()
    print(f"[verify-batch] Multi-sheet Excel workbook exported to: {ex_stats['file']} "
          f"(Verified: {ex_stats['verified']}, Rejected: {ex_stats['rejected']}, "
          f"Dead: {ex_stats['dead']}, Blocked: {ex_stats['blocked']})")


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[Presence] Interrupted by user. Shutting down gracefully...")
        sys.exit(0)

if __name__ == "__main__":
    main()
