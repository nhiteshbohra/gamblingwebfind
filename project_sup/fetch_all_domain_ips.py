"""
project_sup/fetch_all_domain_ips.py
───────────────────────────────────
High-speed asynchronous DNS resolver to populate the 'ip' field across
MongoDB collections: 'checked_domains', 'domain_Listed', or 'both'.

Features:
  • Multi-threaded / Async DNS resolver with custom public nameservers (8.8.8.8, 1.1.1.1).
  • High concurrency (default 100 workers) with batched bulk MongoDB writes.
  • Dynamic tqdm progress monitoring with real-time resolved vs. failed metrics.
  • Cross-collection sync: Copies existing resolved IPs from checked_domains to domain_Listed.

Usage:
  # 1. Populate domain_Listed (syncs from checked_domains first, then resolves remaining)
  python project_sup/fetch_all_domain_ips.py --target domain_Listed

  # 2. Populate checked_domains (default)
  python project_sup/fetch_all_domain_ips.py --target checked_domains

  # 3. Populate BOTH collections
  python project_sup/fetch_all_domain_ips.py --target both --concurrency 150
"""

import sys
import os
import asyncio
import socket
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from pymongo import UpdateOne
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, get_db

try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False


def _create_custom_resolver():
    if HAS_DNSPYTHON:
        res = dns.resolver.Resolver(configure=True)
        res.nameservers = ["8.8.8.8", "1.1.1.1", "8.8.4.4", "1.0.0.1"]
        res.timeout = 2.0
        res.lifetime = 2.5
        return res
    return None


def resolve_domain_ip_sync(domain: str, resolver=None) -> str | list[str] | None:
    """Resolve IPv4 address for a domain cleanly. Returns list if multiple IPs, string if single."""
    clean = (
        domain.removeprefix("https://")
        .removeprefix("http://")
        .split("/")[0]
        .split(":")[0]
        .strip()
    )
    if not clean:
        return None

    if resolver and HAS_DNSPYTHON:
        try:
            answers = resolver.resolve(clean, "A")
            ips = list(dict.fromkeys([str(rdata.address) for rdata in answers]))
            if len(ips) > 1:
                return ips
            elif len(ips) == 1:
                return ips[0]
        except Exception:
            pass

    # Fallback to standard socket resolution
    try:
        addrinfo = socket.getaddrinfo(clean, None, socket.AF_INET)
        ips = list(dict.fromkeys([item[4][0] for item in addrinfo if item and item[4]]))
        if len(ips) > 1:
            return ips
        elif len(ips) == 1:
            return ips[0]
    except Exception:
        pass
    return None


def sync_ips_checked_to_listed():
    """Fast copy of IPs already resolved in checked_domains into domain_Listed."""
    src_col = source_domains()
    chk_col = checked_domains()

    query = {"ip": {"$nin": [None, ""]}}
    total_checked = chk_col.count_documents(query)
    if total_checked == 0:
        return 0

    print(f"\n[+] Fast-syncing {total_checked:,} resolved IPs from 'checked_domains' into 'domain_Listed'...")
    cursor = chk_col.find(query, {"_id": 1, "ip": 1})

    bulk_ops = []
    synced_count = 0
    for doc in cursor:
        bulk_ops.append(
            UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"ip": doc["ip"]}}
            )
        )
        if len(bulk_ops) >= 1000:
            res = src_col.bulk_write(bulk_ops, ordered=False)
            synced_count += res.modified_count
            bulk_ops = []

    if bulk_ops:
        res = src_col.bulk_write(bulk_ops, ordered=False)
        synced_count += res.modified_count

    print(f"[+] Instant sync complete: Updated {synced_count:,} records in 'domain_Listed'.\n")
    return synced_count


async def run_ip_population_for_collection(
    target_collection_name: str,
    concurrency: int = 100,
    batch_size: int = 500,
    status_filter: str = "all",
    limit: int = 0
):
    get_db()
    if target_collection_name == "domain_Listed":
        col = source_domains()
    else:
        col = checked_domains()

    # Query documents where 'ip' is missing or null
    query = {"$or": [{"ip": {"$exists": False}}, {"ip": None}, {"ip": ""}]}
    if status_filter and status_filter != "all" and target_collection_name == "checked_domains":
        query["status"] = status_filter

    total_pending = col.count_documents(query)
    print(f"\n[+] Total documents in '{target_collection_name}' missing 'ip': {total_pending:,}")
    if total_pending == 0:
        print(f"[+] All documents in '{target_collection_name}' already have an IP address populated!\n")
        return

    cursor = col.find(query, {"_id": 1, "domain": 1, "url": 1})
    if limit > 0:
        cursor = cursor.limit(limit)
        total_pending = limit

    print(f"[+] Launching DNS resolver pool on '{target_collection_name}' (workers={concurrency}, batch_size={batch_size})...\n")

    resolver = _create_custom_resolver()
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=concurrency)

    stats = {
        "processed": 0,
        "resolved": 0,
        "unresolved": 0,
        "written": 0,
    }

    pending_writes = []
    lock = asyncio.Lock()

    async def flush_writes():
        nonlocal pending_writes
        if not pending_writes:
            return
        to_write = pending_writes
        pending_writes = []
        try:
            res = await asyncio.to_thread(col.bulk_write, to_write, ordered=False)
            stats["written"] += res.modified_count + res.upserted_count
        except Exception as e:
            print(f"\n[!] Bulk write error: {e}")

    pbar = tqdm(total=total_pending, desc=f"Resolving ({target_collection_name})", unit="dom", dynamic_ncols=True)
    sem = asyncio.Semaphore(concurrency)

    async def worker(doc: dict):
        domain = doc.get("domain") or doc.get("_id")
        doc_id = doc["_id"]

        async with sem:
            ip = await loop.run_in_executor(executor, resolve_domain_ip_sync, domain, resolver)

        async with lock:
            stats["processed"] += 1
            if ip:
                stats["resolved"] += 1
                pending_writes.append(
                    UpdateOne({"_id": doc_id}, {"$set": {"ip": ip}})
                )
            else:
                stats["unresolved"] += 1
                pending_writes.append(
                    UpdateOne({"_id": doc_id}, {"$set": {"ip": None}})
                )

            if len(pending_writes) >= batch_size:
                await flush_writes()

            pbar.update(1)
            pbar.set_postfix(
                resolved=f"{stats['resolved']:,}",
                dead=f"{stats['unresolved']:,}",
                saved=f"{stats['written']:,}",
            )

    # Process in chunks to manage memory
    chunk_size = 5000
    chunk = []
    for doc in cursor:
        chunk.append(doc)
        if len(chunk) >= chunk_size:
            tasks = [asyncio.create_task(worker(d)) for d in chunk]
            await asyncio.gather(*tasks)
            chunk = []

    if chunk:
        tasks = [asyncio.create_task(worker(d)) for d in chunk]
        await asyncio.gather(*tasks)

    # Final flush
    async with lock:
        await flush_writes()

    pbar.close()
    executor.shutdown(wait=False)

    print("\n" + "=" * 60)
    print(f"        DNS IP POPULATION COMPLETE ({target_collection_name})")
    print("=" * 60)
    print(f" Total Processed       : {stats['processed']:,}")
    print(f" Successfully Resolved : {stats['resolved']:,}")
    print(f" Unresolvable (Dead)   : {stats['unresolved']:,}")
    print(f" Saved to MongoDB      : {stats['written']:,}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Populate IP address field across MongoDB documents.")
    parser.add_argument(
        "--target",
        type=str,
        choices=["checked_domains", "domain_Listed", "both"],
        default="checked_domains",
        help="Target collection to populate (choices: checked_domains, domain_Listed, both; default: checked_domains)"
    )
    parser.add_argument("--concurrency", type=int, default=100, help="Concurrent DNS worker threads (default: 100)")
    parser.add_argument("--batch-size", type=int, default=500, help="Bulk MongoDB write batch size (default: 500)")
    parser.add_argument("--status", type=str, default="all", help="Filter by status for checked_domains (default: all)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of domains to process (0 = all)")

    args = parser.parse_args()

    try:
        if args.target == "domain_Listed":
            # 1. Fast sync existing IPs first
            sync_ips_checked_to_listed()
            # 2. Resolve any remaining missing ones
            asyncio.run(
                run_ip_population_for_collection(
                    target_collection_name="domain_Listed",
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                    status_filter="all",
                    limit=args.limit,
                )
            )
        elif args.target == "both":
            # 1. Run on checked_domains
            asyncio.run(
                run_ip_population_for_collection(
                    target_collection_name="checked_domains",
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                    status_filter=args.status,
                    limit=args.limit,
                )
            )
            # 2. Sync to domain_Listed
            sync_ips_checked_to_listed()
            # 3. Run on domain_Listed for remaining
            asyncio.run(
                run_ip_population_for_collection(
                    target_collection_name="domain_Listed",
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                    status_filter="all",
                    limit=args.limit,
                )
            )
        else:
            # Default: checked_domains only
            asyncio.run(
                run_ip_population_for_collection(
                    target_collection_name="checked_domains",
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                    status_filter=args.status,
                    limit=args.limit,
                )
            )
    except KeyboardInterrupt:
        print("\n[!] Process interrupted by user.")


if __name__ == "__main__":
    main()
