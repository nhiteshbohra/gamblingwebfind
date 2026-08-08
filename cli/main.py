import os
import sys
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

    else:
        parser.print_help()

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[Presence] Stopped by user.")

if __name__ == "__main__":
    main()
