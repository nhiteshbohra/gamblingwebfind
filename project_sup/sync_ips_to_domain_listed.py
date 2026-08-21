"""
project_sup/sync_ips_to_domain_listed.py
────────────────────────────────────────
Fast sync utility to copy resolved 'ip' addresses from 'checked_domains'
into 'domain_Listed' using high-speed MongoDB batching.

Also includes an optional DNS resolver flag (--resolve-remaining) to resolve
IPs for any remaining domains in domain_Listed that were never in checked_domains.

Usage:
  # Fast copy all existing IPs from checked_domains to domain_Listed
  python project_sup/sync_ips_to_domain_listed.py

  # Fast copy + resolve any remaining missing IPs via DNS
  python project_sup/sync_ips_to_domain_listed.py --resolve-remaining --concurrency 100
"""

import sys
import os
import argparse
import socket
from pathlib import Path
from pymongo import UpdateOne
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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


def sync_ips_from_checked_to_listed(batch_size: int = 1000):
    get_db()
    chk_col = checked_domains()
    src_col = source_domains()

    # Find all documents in checked_domains where ip is valid
    query = {"ip": {"$nin": [None, ""]}}
    total = chk_col.count_documents(query)

    print(f"\n[+] Found {total:,} documents with resolved IPs in 'checked_domains'.")
    if total == 0:
        print("[!] No resolved IPs found in 'checked_domains'.\n")
        return 0

    print(f"[+] Starting high-speed batch sync to 'domain_Listed' (batch_size={batch_size})...\n")

    cursor = chk_col.find(query, {"_id": 1, "domain": 1, "ip": 1})
    pbar = tqdm(total=total, desc="Syncing IPs to domain_Listed", unit="dom", dynamic_ncols=True)

    bulk_ops = []
    total_synced = 0

    for doc in cursor:
        domain_id = doc["_id"]
        ip_val = doc["ip"]

        bulk_ops.append(
            UpdateOne(
                {"_id": domain_id},
                {"$set": {"ip": ip_val}}
            )
        )

        if len(bulk_ops) >= batch_size:
            try:
                res = src_col.bulk_write(bulk_ops, ordered=False)
                total_synced += res.modified_count
            except Exception as e:
                print(f"\n[!] Bulk write error: {e}")
            bulk_ops = []

        pbar.update(1)

    if bulk_ops:
        try:
            res = src_col.bulk_write(bulk_ops, ordered=False)
            total_synced += res.modified_count
        except Exception as e:
            print(f"\n[!] Final bulk write error: {e}")

    pbar.close()

    print("\n" + "=" * 60)
    print("        IP SYNC COMPLETE")
    print("=" * 60)
    print(f" Total Checked IPs Processed : {total:,}")
    print(f" Documents Updated in Listed : {total_synced:,}")
    print("=" * 60 + "\n")
    return total_synced


def resolve_remaining_listed_ips(concurrency: int = 100, batch_size: int = 500):
    """Resolve DNS for any domain_Listed documents that still don't have an IP."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    get_db()
    src_col = source_domains()

    query = {"$or": [{"ip": {"$exists": False}}, {"ip": None}, {"ip": ""}]}
    total_missing = src_col.count_documents(query)

    print(f"[+] Remaining documents in 'domain_Listed' missing IP: {total_missing:,}")
    if total_missing == 0:
        print("[+] All documents in 'domain_Listed' already have an IP!\n")
        return

    print(f"[+] Resolving remaining {total_missing:,} domains via DNS (concurrency={concurrency})...\n")

    cursor = src_col.find(query, {"_id": 1, "domain": 1})
    resolver = _create_custom_resolver()
    executor = ThreadPoolExecutor(max_workers=concurrency)

    stats = {"processed": 0, "resolved": 0, "unresolved": 0, "written": 0}
    pending_writes = []

    async def run_async_dns():
        loop = asyncio.get_running_loop()
        lock = asyncio.Lock()
        sem = asyncio.Semaphore(concurrency)
        pbar = tqdm(total=total_missing, desc="Resolving Remaining IPs", unit="dom", dynamic_ncols=True)

        async def flush():
            nonlocal pending_writes
            if not pending_writes:
                return
            to_write = pending_writes
            pending_writes = []
            try:
                res = await asyncio.to_thread(src_col.bulk_write, to_write, ordered=False)
                stats["written"] += res.modified_count
            except Exception as e:
                print(f"\n[!] Bulk write error: {e}")

        async def worker(doc):
            domain = doc.get("domain") or doc.get("_id")
            doc_id = doc["_id"]
            async with sem:
                ip = await loop.run_in_executor(executor, resolve_domain_ip_sync, domain, resolver)

            async with lock:
                stats["processed"] += 1
                if ip:
                    stats["resolved"] += 1
                    pending_writes.append(UpdateOne({"_id": doc_id}, {"$set": {"ip": ip}}))
                else:
                    stats["unresolved"] += 1
                    pending_writes.append(UpdateOne({"_id": doc_id}, {"$set": {"ip": None}}))

                if len(pending_writes) >= batch_size:
                    await flush()

                pbar.update(1)
                pbar.set_postfix(resolved=f"{stats['resolved']:,}", dead=f"{stats['unresolved']:,}", saved=f"{stats['written']:,}")

        # Process in chunks
        chunk = []
        for doc in cursor:
            chunk.append(doc)
            if len(chunk) >= 5000:
                tasks = [asyncio.create_task(worker(d)) for d in chunk]
                await asyncio.gather(*tasks)
                chunk = []

        if chunk:
            tasks = [asyncio.create_task(worker(d)) for d in chunk]
            await asyncio.gather(*tasks)

        async with lock:
            await flush()

        pbar.close()

    asyncio.run(run_async_dns())
    executor.shutdown(wait=False)
    print(f"\n[✓] Finished remaining DNS resolution: {stats['resolved']:,} resolved, {stats['written']:,} saved.")


def main():
    parser = argparse.ArgumentParser(description="Sync resolved IPs from checked_domains to domain_Listed.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for bulk updates (default: 1000)")
    parser.add_argument("--resolve-remaining", action="store_true", help="Also resolve DNS for domains in domain_Listed not found in checked_domains")
    parser.add_argument("--concurrency", type=int, default=100, help="DNS concurrency if --resolve-remaining is enabled (default: 100)")

    args = parser.parse_args()

    # Step 1: Instant sync from checked_domains
    sync_ips_from_checked_to_listed(batch_size=args.batch_size)

    # Step 2: Optional resolve remaining
    if args.resolve_remaining:
        resolve_remaining_listed_ips(concurrency=args.concurrency, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
