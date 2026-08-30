"""
list_fetcher/keysfetch_from_txt.py
───────────────────────────────────────
Fetches, cleans, normalizes, and imports gambling/cheating blocklists from GitHub
and external text files into MongoDB (checked_domains & domain_Listed).

Configured Sources:
  1. Estonia Blocked Gambling Websites (elliotwutingfeng)
  2. TEQSA Illegal Cheating Websites (elliotwutingfeng)
  3. ACMA Blocked Gambling Websites (elliotwutingfeng)
  4. Arkynx Gambling Domains (arkynx)
  5. AdGuardHome Gambling Filter (alexsannikov)
  6. ph00lt0 Blocklist Domains (ph00lt0)
  7. Eimji Gambling Hosts (Eimji)
  8. BlocklistProject Gambling List (blocklistproject)
  9. Hagezi DNS Gambling Blocklist (hagezi) - Optional / Built-in
"""

import argparse
import concurrent.futures
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Generator, Iterable, List, Optional, Set, Tuple

# ── Ensure project root on sys.path ──────────────────────────────────────────
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(_ROOT) / ".env")

import tldextract
from pymongo import UpdateOne
from tqdm import tqdm

from db.mongo_client import (
    checked_domains,
    source_domains,
    resolve_ip,
    resolve_asn,
    IST,
)

_SOURCES_FILE = Path(__file__).resolve().parent / "sources.txt"


def load_sources_from_file(path: Path = _SOURCES_FILE) -> List[dict]:
    """Load blocklist sources from sources.txt.

    sources.txt is the single source of truth — all URLs live there.
    Edit that file to add, remove, or change sources; no code changes needed.

    Format (one per line):
        id | Name | URL_or_local_path

    Lines starting with # or blank lines are ignored.
    Raises FileNotFoundError if sources.txt is missing.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"sources.txt not found at {path}\n"
            "Create it with lines in the format:  id | Name | URL_or_local_path"
        )

    sources = []
    seen_ids = set()

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"[sources.txt] Could not read {path}: {e}")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) < 3:
            print(f"[sources.txt] Skipping malformed line (need id | Name | URL): {raw_line!r}")
            continue
        sid, name, url = parts
        if not sid or not url:
            continue
        if sid in seen_ids:
            print(f"[sources.txt] Duplicate id '{sid}' — keeping first occurrence, skipping repeat")
            continue
        seen_ids.add(sid)
        sources.append({
            "id": sid,
            "name": name,
            "url": url,
            "category": "gambling",
            "status": "gambling",
            "reason_prefix": name,
        })

    if not sources:
        raise ValueError(f"[sources.txt] No valid sources found in {path}")

    return sources




def github_blob_to_raw_url(url: str) -> str:
    """
    Convert GitHub web URLs (containing /blob/) to direct raw URLs.
    Example:
      https://github.com/owner/repo/blob/main/path.txt
      -> https://raw.githubusercontent.com/owner/repo/main/path.txt
    """
    url = url.strip()
    if "github.com/" in url and "/blob/" in url:
        raw = url.replace("https://github.com/", "https://raw.githubusercontent.com/")
        raw = raw.replace("http://github.com/", "https://raw.githubusercontent.com/")
        raw = raw.replace("/blob/", "/")
        return raw
    return url



# Custom pre-compiled regex patterns for speed
_ADBLOCK_PREFIX = re.compile(r"^[|@]+")
_ADBLOCK_SUFFIX = re.compile(r"[\^$].*$")
_IP_PREFIX = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1|::|\d{1,3}(?:\.\d{1,3}){3})\s+")
_DNSMASQ_PREFIX = re.compile(r"^(?:address|server|local|ipset|nftset)=/", re.IGNORECASE)
_RPZ_EXTRA = re.compile(r"\s+(?:CNAME|A|AAAA|TXT)\s+.*$", re.IGNORECASE)
_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9.\-_]")


def clean_line_to_domain(line: str) -> Optional[str]:
    """
    Clean, parse and normalize a single line from any blocklist format
    (Hosts, AdBlock, RPZ, Dnsmasq, Wildcard, URL, or plain domain) into a clean domain name.
    """
    if not line:
        return None
    raw = line.strip()
    if not raw or raw.startswith(("#", "!", ";", "//", "[", "<")):
        return None

    # Strip dnsmasq syntax (local=/domain.com/ or address=/domain.com/0.0.0.0)
    raw = _DNSMASQ_PREFIX.sub("", raw)
    if "/" in raw and not raw.startswith(("http://", "https://")):
        raw = raw.strip("/").split("/")[0].strip()

    # Strip RPZ syntax (e.g. 'domain.com CNAME .' or '*.domain.com A 0.0.0.0')
    raw = _RPZ_EXTRA.sub("", raw).strip()

    # Strip hosts format IP prefix (e.g. '0.0.0.0 domain.com' -> 'domain.com')
    raw = _IP_PREFIX.sub("", raw).strip()

    # Strip Adblock / Adguard modifiers (e.g. '||domain.com^$third-party' -> 'domain.com')
    raw = _ADBLOCK_PREFIX.sub("", raw)
    raw = _ADBLOCK_SUFFIX.sub("", raw)

    # Strip leading wildcards (*.domain.com -> domain.com)
    raw = re.sub(r"^\*\.+", "", raw)

    # Strip whitespace, quotes, commas, trailing slash, brackets
    raw = raw.strip(" '\"/\\,;()[]{}*^")

    # If full URL, take hostname
    if "://" in raw:
        try:
            parsed = urllib.parse.urlparse(raw)
            raw = parsed.netloc or parsed.path
        except Exception:
            pass

    # Extract first token if multiple separated by space or tab
    if " " in raw or "\t" in raw:
        raw = raw.replace("\t", " ").split()[0].strip()

    # Remove port if present (e.g. domain.com:80)
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()

    # Strip www prefix
    if raw.startswith("www."):
        raw = raw[4:]

    # Remove trailing dot (DNS zone format e.g. domain.com.)
    raw = raw.rstrip(".")

    if not raw or len(raw) < 4 or "." not in raw:
        return None

    # Discard non-domain entries, localhost, broadcast, or invalid characters
    if raw in ("localhost", "local", "broadcasthost", "ip6-localhost", "ip6-loopback"):
        return None
    if _INVALID_CHARS.search(raw):
        return None

    # Extract registered domain via tldextract
    ext = tldextract.extract(raw)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()

    return raw.lower()


def fetch_raw_content(url_or_path: str, timeout: int = 30) -> str:
    """
    Fetch content from URL (HTTP/HTTPS) or read local file path.
    """
    url_or_path = url_or_path.strip()
    if url_or_path.startswith(("http://", "https://")):
        raw_url = github_blob_to_raw_url(url_or_path)
        req = urllib.request.Request(
            raw_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/plain,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            # Try utf-8, fallback to latin-1
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return content.decode("latin-1", errors="ignore")
    else:
        # Local file path
        p = Path(url_or_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {url_or_path}")
        for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                return p.read_text(encoding=enc)
            except Exception:
                continue
        return p.read_text(encoding="utf-8", errors="ignore")


def fetch_and_parse_blocklist(
    url_or_path: str,
    limit: int = 0,
) -> Tuple[List[str], int]:
    """
    Fetch and parse blocklist content into a deduplicated list of valid domains.
    Returns: (list_of_domains, total_raw_lines)
    """
    content = fetch_raw_content(url_or_path)
    lines = content.splitlines()
    total_lines = len(lines)

    seen: Set[str] = set()
    domains: List[str] = []

    for line in lines:
        dom = clean_line_to_domain(line)
        if dom and dom not in seen:
            seen.add(dom)
            domains.append(dom)
            if limit and len(domains) >= limit:
                break

    return domains, total_lines


def batch_chunks(iterable: Iterable, size: int) -> Generator[list, None, None]:
    """Yield successive chunks of size n from iterable."""
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def import_blocklists_to_mongo(
    sources: Optional[List[dict]] = None,
    custom_urls: Optional[List[str]] = None,
    custom_files: Optional[List[str]] = None,
    status: str = "gambling",
    resolve_dns_records: bool = False,
    chunk_size: int = 2500,
    limit_per_source: int = 0,
    skip_existing: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Main orchestration function to fetch blocklists and bulk save to MongoDB
    with strict multi-tier deduplication:
      1. Intra-source deduplication (dedup within file)
      2. Inter-source deduplication (dedup across all blocklist sources in run)
      3. Database deduplication (skip domains already existing in checked_domains)
    """
    today_date = datetime.now(IST).strftime("%d-%m-%Y")
    stats = {
        "sources_processed": 0,
        "raw_lines_read": 0,
        "unique_domains_found": 0,
        "skipped_duplicate_sources": 0,
        "skipped_already_in_db": 0,
        "checked_domains_inserted": 0,
        "checked_domains_updated": 0,
        "domain_listed_upserted": 0,
        "errors": [],
    }

    all_sources = list(sources) if sources is not None else load_sources_from_file()


    # Append any custom URLs provided
    if custom_urls:
        for idx, u in enumerate(custom_urls):
            all_sources.append({
                "id": f"custom_url_{idx + 1}",
                "name": f"Custom URL: {u.split('/')[-1] or u}",
                "url": u,
                "category": "gambling",
                "status": status,
                "reason_prefix": "Custom External Blocklist",
            })

    # Append any custom local files provided
    if custom_files:
        for idx, fpath in enumerate(custom_files):
            all_sources.append({
                "id": f"custom_file_{idx + 1}",
                "name": f"Local File: {Path(fpath).name}",
                "url": fpath,
                "category": "gambling",
                "status": status,
                "reason_prefix": "Local Blocklist File",
            })

    print(f"\n🚀 Starting Blocklist Import Pipeline ({len(all_sources)} sources)")
    print(f"   Status: {status} | Fast Mode: {not resolve_dns_records} | Skip Existing in DB: {skip_existing} | Dry Run: {dry_run}")
    print("=" * 70)

    checked_col = None if dry_run else checked_domains()
    source_col = None if dry_run else source_domains()

    total_unique_seen_global: Set[str] = set()

    for src in all_sources:
        src_id = src.get("id", "blocklist")
        src_name = src.get("name", src_id)
        src_url = src.get("url", "")
        src_status = src.get("status", status)
        reason_prefix = src.get("reason_prefix", "Blocklist import")

        print(f"\n📥 Fetching [{src_name}] ...")
        print(f"   URL/Path: {src_url}")

        start_t = time.time()
        try:
            raw_domains, raw_lines = fetch_and_parse_blocklist(src_url, limit=limit_per_source)
        except Exception as e:
            err_msg = f"Failed to fetch {src_name} ({src_url}): {e}"
            print(f"   ❌ Error: {err_msg}")
            stats["errors"].append(err_msg)
            continue

        elapsed = time.time() - start_t
        stats["sources_processed"] += 1
        stats["raw_lines_read"] += raw_lines

        # Deduplicate across sources in current session
        new_domains_for_src = []
        dup_count = 0
        for d in raw_domains:
            if d not in total_unique_seen_global:
                total_unique_seen_global.add(d)
                new_domains_for_src.append(d)
            else:
                dup_count += 1

        stats["skipped_duplicate_sources"] += dup_count
        stats["unique_domains_found"] += len(new_domains_for_src)

        print(
            f"   ✅ Parsed {len(raw_domains):,} unique domains in file "
            f"({dup_count:,} duplicate across prior sources, {len(new_domains_for_src):,} fresh) "
            f"in {elapsed:.2f}s"
        )

        if not new_domains_for_src:
            print("   ⏩ All domains from this source were already seen in prior sources. Skipping.")
            continue

        if dry_run:
            print(f"   [Dry Run] Would check and write up to {len(new_domains_for_src):,} domains to MongoDB.")
            continue

        # Prepare bulk writes
        print(f"   💾 Writing {len(new_domains_for_src):,} domains to MongoDB...")
        pbar = tqdm(total=len(new_domains_for_src), desc=f"   Saving {src_id}", unit="domains", ncols=80)

        for chunk in batch_chunks(new_domains_for_src, chunk_size):
            # Check existing domains in MongoDB if skip_existing is True
            domains_to_write = chunk
            if skip_existing:
                existing_in_db = set(
                    doc["_id"] for doc in source_col.find(
                        {"_id": {"$in": chunk}},
                        {"_id": 1}
                    )
                )
                if existing_in_db:
                    stats["skipped_already_in_db"] += len(existing_in_db)
                    domains_to_write = [d for d in chunk if d not in existing_in_db]

            if not domains_to_write:
                pbar.update(len(chunk))
                continue

            checked_ops = []
            source_ops = []

            for d in domains_to_write:
                ip_val = None
                asn_val = None

                if resolve_dns_records:
                    try:
                        ip_val = resolve_ip(d)
                        if ip_val:
                            single_ip = ip_val[0] if isinstance(ip_val, list) else ip_val
                            asn_val = resolve_asn(single_ip)
                    except Exception:
                        pass

                checked_doc = {
                    "domain": d,
                    "url": f"https://{d}",
                    "status": src_status,
                    "reason": f"Known blocklist: {reason_prefix}",
                    "screenshot_taken": False,
                    "source": f"blocklist:{src_id}",
                    "decided_by": "external_blocklist",
                }
                if ip_val:
                    checked_doc["ip"] = ip_val
                if asn_val:
                    checked_doc["asn"] = asn_val

                # Write to checked_domains
                checked_ops.append(
                    UpdateOne(
                        {"_id": d},
                        {
                            "$set": checked_doc,
                            "$setOnInsert": {"added_date": today_date},
                        },
                        upsert=True,
                    )
                )

                # Sync to domain_Listed with strict schema:
                # { _id, domain, added_date, active: True, source: "GITHUB FETCH <date>", processed: False }
                source_ops.append(
                    UpdateOne(
                        {"_id": d},
                        {
                            "$set": {
                                "domain": d,
                                "active": True,
                                "processed": False,
                                "source": f"GITHUB FETCH {today_date}",
                            },
                            "$setOnInsert": {"added_date": today_date},
                        },
                        upsert=True,
                    )
                )

            if checked_ops:
                res_checked = checked_col.bulk_write(checked_ops, ordered=False)
                res_source = source_col.bulk_write(source_ops, ordered=False)

                stats["checked_domains_inserted"] += res_checked.upserted_count
                stats["checked_domains_updated"] += res_checked.modified_count
                stats["domain_listed_upserted"] += (res_source.upserted_count + res_source.modified_count)

            pbar.update(len(chunk))

        pbar.close()

    print("\n" + "=" * 70)
    print("✨ Blocklist Import Completed Summary:")
    print(f"   • Total Sources Processed      : {stats['sources_processed']}")
    print(f"   • Total Raw Lines Read         : {stats['raw_lines_read']:,}")
    print(f"   • Total Global Unique Domains  : {len(total_unique_seen_global):,}")
    print(f"   • Skipped (Cross-Source Dups)  : {stats['skipped_duplicate_sources']:,}")
    print(f"   • Skipped (Already in MongoDB) : {stats['skipped_already_in_db']:,}")
    print(f"   • Newly Inserted to MongoDB    : {stats['checked_domains_inserted']:,}")
    if stats["checked_domains_updated"]:
        print(f"   • Updated in MongoDB           : {stats['checked_domains_updated']:,}")
    print(f"   • domain_Listed Synced         : {stats['domain_listed_upserted']:,}")
    if stats["errors"]:
        print(f"   • Errors encountered           : {len(stats['errors'])}")
        for err in stats["errors"]:
            print(f"     - {err}")
    print("=" * 70 + "\n")

    return stats


def print_database_stats():
    """Print current MongoDB collection statistics for checked_domains and domain_Listed."""
    try:
        col_checked = checked_domains()
        col_source = source_domains()

        print("\n📊 MongoDB Current Statistics:")
        print(f"   • Database: {col_checked.database.name}")
        print(f"   • checked_domains total docs : {col_checked.count_documents({}):,}")
        print(f"   • domain_Listed total docs   : {col_source.count_documents({}):,}")

        print("\n   [checked_domains by status]:")
        pipeline_status = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        for row in col_checked.aggregate(pipeline_status):
            status_name = row.get("_id") or "unspecified"
            print(f"     - {status_name:<15}: {row['count']:,}")

        print("\n   [checked_domains by source (top 10)]:")
        pipeline_source = [
            {"$group": {"_id": "$source", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        for row in col_checked.aggregate(pipeline_source):
            src_name = row.get("_id") or "unspecified"
            print(f"     - {src_name:<30}: {row['count']:,}")
        print()
    except Exception as e:
        print(f"❌ Failed to fetch database statistics: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch, normalize and save blocklists into MongoDB checked_domains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import all default 8 blocklists directly into MongoDB (Fast mode)
  python -m list_fetcher.keysfetch_from_txt

  # Dry run preview with domain counts
  python -m list_fetcher.keysfetch_from_txt --dry-run

  # Import with DNS IP and ASN resolution
  python -m list_fetcher.keysfetch_from_txt --resolve-dns

  # Import only specific sources by index or ID (e.g. source 1, 3, 5)
  python -m list_fetcher.keysfetch_from_txt --sources estonia_gambling acma_gambling

  # Import from a custom URL or local text file
  python -m list_fetcher.keysfetch_from_txt --custom-url https://example.com/blocklist.txt
  python -m list_fetcher.keysfetch_from_txt --custom-file path/to/my_domains.txt

  # Show database stats
  python -m list_fetcher.keysfetch_from_txt --stats
        """,
    )

    parser.add_argument(
        "--sources",
        nargs="*",
        help="Filter specific source IDs (e.g. estonia_gambling, acma_gambling, etc.)",
    )
    parser.add_argument(
        "--custom-url",
        action="append",
        dest="custom_urls",
        help="Add additional custom blocklist URL(s) to fetch",
    )
    parser.add_argument(
        "--custom-file",
        action="append",
        dest="custom_files",
        help="Add additional custom local text file(s) to parse and import",
    )
    parser.add_argument(
        "--status",
        default="gambling",
        help="Status to assign in checked_domains (default: 'gambling')",
    )
    parser.add_argument(
        "--resolve-dns",
        action="store_true",
        help="Enable live DNS IP and ASN resolution during import (slower)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2500,
        help="MongoDB bulk write batch size (default: 2500)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of domains per source (for testing/partial runs)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse blocklists without writing to MongoDB",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite/update domains if they already exist in MongoDB checked_domains (default: skip existing)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print current MongoDB statistics and exit",
    )

    args = parser.parse_args()

    if args.stats:
        print_database_stats()
        return

    # Filter sources if requested
    selected_sources = BLOCKLIST_SOURCES
    if args.sources:
        selected_sources = [
            s for s in BLOCKLIST_SOURCES
            if s["id"] in args.sources or any(src_filter.lower() in s["id"].lower() for src_filter in args.sources)
        ]
        if not selected_sources and not args.custom_urls and not args.custom_files:
            print(f"⚠️ No matching sources found for filter: {args.sources}")
            print(f"Available sources: {[s['id'] for s in BLOCKLIST_SOURCES]}")
            return

    import_blocklists_to_mongo(
        sources=selected_sources,
        custom_urls=args.custom_urls,
        custom_files=args.custom_files,
        status=args.status,
        resolve_dns_records=args.resolve_dns,
        chunk_size=args.chunk_size,
        limit_per_source=args.limit,
        skip_existing=not args.overwrite,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
