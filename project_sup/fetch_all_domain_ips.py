"""
project_sup/fetch_all_domain_ips.py
───────────────────────────────────
High-speed asynchronous DNS resolver to populate the 'ip' field across all
existing documents in MongoDB 'checked_domains'.

Features:
  • Multi-threaded / Async DNS resolver with custom public nameservers (8.8.8.8, 1.1.1.1).
  • High concurrency (default 100 workers) with batched bulk MongoDB writes.
  • Dynamic tqdm progress monitoring with real-time resolved vs. failed metrics.
  • Safe resume: Only processes documents where 'ip' is missing or null.

Usage:
  python project_sup/fetch_all_domain_ips.py
  python project_sup/fetch_all_domain_ips.py --concurrency 150 --status gambling
  python project_sup/fetch_all_domain_ips.py --status all --limit 1000
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

from db.mongo_client import checked_domains, get_db

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


async def run_ip_population(concurrency: int = 100, batch_size: int = 500, status_filter: str = "all", limit: int = 0):
    get_db()
    col = checked_domains()

    # Query documents where 'ip' is missing or null
    query = {"$or": [{"ip": {"$exists": False}}, {"ip": None}, {"ip": ""}]}
    if status_filter and status_filter != "all":
        query["status"] = status_filter

    total_pending = col.count_documents(query)
    print(f"\n[+] Total documents in checked_domains missing 'ip' (status='{status_filter}'): {total_pending:,}")
    if total_pending == 0:
        print("[+] All documents already have an IP address populated! Nothing to do.\n")
        return

    cursor = col.find(query, {"_id": 1, "domain": 1, "url": 1})
    if limit > 0:
        cursor = cursor.limit(limit)
        total_pending = limit

    print(f"[+] Launching DNS resolver pool with concurrency={concurrency} (batch_size={batch_size})...\n")

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

    pbar = tqdm(total=total_pending, desc="Resolving IPs", unit="dom", dynamic_ncols=True)

    sem = asyncio.Semaphore(concurrency)

    async def worker(doc: dict):
        domain = doc.get("domain") or doc.get("_id")
        doc_id = doc["_id"]

        async with sem:
            # Resolve IP in thread pool
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
                # Mark as null or leave unset so it doesn't loop infinitely
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
    print("           DNS IP POPULATION COMPLETE")
    print("=" * 60)
    print(f" Total Processed  : {stats['processed']:,}")
    print(f" Successfully Resolved : {stats['resolved']:,}")
    print(f" Unresolvable (Dead)   : {stats['unresolved']:,}")
    print(f" Saved to MongoDB      : {stats['written']:,}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Populate IP address field for all MongoDB documents.")
    parser.add_argument("--concurrency", type=int, default=100, help="Concurrent DNS worker threads (default: 100)")
    parser.add_argument("--batch-size", type=int, default=500, help="Bulk MongoDB write batch size (default: 500)")
    parser.add_argument("--status", type=str, default="all", help="Filter by status (e.g. gambling, blocked, regular, dead, all)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of domains to process (0 = all)")

    args = parser.parse_args()

    try:
        asyncio.run(
            run_ip_population(
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
