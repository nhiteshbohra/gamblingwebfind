import argparse
import asyncio
import gc
import gzip
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiohttp
import duckdb
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from tqdm import tqdm

# Load .env from project root (one level up)
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# Custom User-Agent to avoid generic scraper blocking
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

CRAWL = "CC-MAIN-2024-22"
BASE_URL = (
    f"https://data.commoncrawl.org/"
    f"cc-index/table/cc-main/warc/"
    f"crawl={CRAWL}/subset=warc/"
)
MANIFEST_NAME = f"{CRAWL}.warc.paths.gz"
CHECKPOINT_FILE = Path("checkpoint.json")
PARQUET_MAX_RETRIES = 3


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

def get_config():
    """Retrieve settings from .env file or default fallbacks."""
    return {
        "mongo_uri": os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
        "db_name": os.getenv("MONGO_DB_NAME", "domain_finder"),
        "collection": os.getenv("MONGO_COLLECTION", "domains"),
        "export_enabled": os.getenv("EXPORT_TO_MONGO", "true").lower() in ("true", "1", "yes"),
        "batch_size": int(os.getenv("MONGO_BATCH_SIZE", "1000")),
        "max_workers": int(os.getenv("MAX_WORKERS", "200")),
        "timeout": float(os.getenv("TIMEOUT", "5.0")),
        "file_batch_size": int(os.getenv("PARQUET_BATCH_SIZE", "20")),
    }


# ─────────────────────────────────────────────
# Checkpoint helpers
# ─────────────────────────────────────────────

def load_checkpoint() -> dict:
    """Load existing checkpoint if available."""
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_checkpoint(data: dict):
    """Persist checkpoint to disk after each completed batch."""
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def clear_checkpoint():
    """Remove checkpoint file after a successful full run."""
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


# ─────────────────────────────────────────────
# MongoDB helpers
# ─────────────────────────────────────────────

def get_mongo_client(timeout_ms: int = 5000) -> MongoClient | None:
    """Connect to MongoDB with ping check and error handling."""
    config = get_config()
    uri = config["mongo_uri"]
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        client.admin.command("ping")
        return client
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        print(f"\n[Warning] Could not connect to MongoDB at '{uri}': {e}")
        print("Skipping MongoDB export (check if MongoDB service is running).")
        return None
    except Exception as e:
        print(f"\n[Warning] Unexpected error connecting to MongoDB: {e}")
        return None


def export_domains_to_mongo(
    domain_results: list[tuple[str, bool | str]], mongo_client: MongoClient | None = None
) -> int:
    """
    Store domain records in MongoDB with the exact document structure:
    {
      "domain": "example.com",
      "active": true / false / "blocked"
    }
    """
    config = get_config()
    if not config["export_enabled"] or not domain_results:
        return 0

    client = mongo_client or get_mongo_client()
    if not client:
        return 0

    db_name = config["db_name"]
    coll_name = config["collection"]
    collection = client[db_name][coll_name]

    try:
        collection.create_index("domain", unique=True)
    except Exception:
        pass

    batch_size = config["batch_size"]
    operations = []
    total_written = 0

    for domain, status in domain_results:
        op = UpdateOne(
            {"_id": domain},
            {"$set": {"_id": domain, "domain": domain, "active": status}},
            upsert=True,
        )
        operations.append(op)

        if len(operations) >= batch_size:
            try:
                collection.bulk_write(operations, ordered=False)
                total_written += len(operations)
            except Exception as e:
                print(f"\n[MongoDB Error] Bulk write error: {e}")
            operations = []

    if operations:
        try:
            collection.bulk_write(operations, ordered=False)
            total_written += len(operations)
        except Exception as e:
            print(f"\n[MongoDB Error] Bulk write error: {e}")

    if not mongo_client:
        client.close()

    return total_written


# ─────────────────────────────────────────────
# Parquet / DuckDB helpers
# ─────────────────────────────────────────────

def get_paths_file() -> Path:
    candidates = [
        Path("manifests") / MANIFEST_NAME,
        Path(MANIFEST_NAME),
        Path("whirlwind-python") / MANIFEST_NAME,
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def get_parquet_files() -> list[str]:
    paths_file = get_paths_file()
    if not paths_file.exists():
        raise FileNotFoundError(
            f"Missing manifest file '{paths_file}'. "
            f"Need {CRAWL}.warc.paths.gz manifest in 'manifests/' folder."
        )

    with gzip.open(paths_file, "rt", encoding="utf-8") as f:
        paths = [line.strip() for line in f if line.strip()]

    return [BASE_URL + path for path in paths]


def query_parquet_batch(
    batch_files: list[str],
    like_clauses: str,
    params: list[str],
    batch_label: str,
) -> list[tuple]:
    """
    Query a batch of parquet files via DuckDB with automatic retry on failure.
    Retries up to PARQUET_MAX_RETRIES times with exponential back-off.
    """
    last_error = None
    con = None
    for attempt in range(1, PARQUET_MAX_RETRIES + 1):
        try:
            con = duckdb.connect()
            con.execute("INSTALL httpfs;")
            con.execute("LOAD httpfs;")
            con.execute("SET http_retries = 20;")
            con.execute("SET enable_progress_bar = false;")

            con.execute(
                f"""
                CREATE OR REPLACE TEMP VIEW ccindex AS
                SELECT url_host_registered_domain, fetch_status
                FROM read_parquet(
                    {batch_files},
                    hive_partitioning=true
                )
                """
            )

            query = f"""
                SELECT DISTINCT
                    url_host_registered_domain AS domain
                FROM ccindex
                WHERE url_host_registered_domain IS NOT NULL
                  AND fetch_status = 200
                  AND ({like_clauses})
            """

            result = con.execute(query, params).fetchall()
            con.close()
            return result

        except Exception as e:
            last_error = e
            try:
                con.close()
            except Exception:
                pass
            if attempt < PARQUET_MAX_RETRIES:
                wait = 2 ** attempt  # 2s, 4s back-off
                print(
                    f"\n  [Retry {attempt}/{PARQUET_MAX_RETRIES}] [{batch_label}] "
                    f"Parquet error: {e}. Retrying in {wait}s..."
                )
                time.sleep(wait)

    print(f"\n  [Error] [{batch_label}] Failed after {PARQUET_MAX_RETRIES} attempts: {last_error}")
    return []


# ─────────────────────────────────────────────
# Async domain evaluation
# ─────────────────────────────────────────────

async def check_dns(domain: str, loop: asyncio.AbstractEventLoop) -> tuple[str, bool]:
    """Stage 1: Fast DNS resolution check."""
    try:
        await loop.getaddrinfo(domain, 80, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        return domain, True
    except Exception:
        return domain, False


async def check_domain_http(
    domain: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, timeout_sec: float
) -> tuple[str, bool | str]:
    """
    Stage 2: HTTP/HTTPS status probe.
    Returns:
    - (domain, True)       -> Live, active website (genuine 2xx response)
    - (domain, "blocked")  -> Blocked by Cloudflare / anti-bot security
    - (domain, False)      -> Unreachable, offline, parked, for-sale, or registrar redirect
    """
    # ── Registrar / parking host domains ──────────────────────────────────
    # If the final URL (after redirects) lands on one of these hosts → inactive
    REGISTRAR_HOSTS = {
        "godaddy.com", "namecheap.com", "name.com", "dynadot.com",
        "sedo.com", "sedoparking.com", "hugedomains.com", "dan.com",
        "afternic.com", "uniregistry.com", "undeveloped.com", "efty.com",
        "squadhelp.com", "brandpa.com", "brandroot.com", "novanym.com",
        "domainmarket.com", "domainnamesales.com", "4.cn", "epik.com",
        "park.io", "bodis.com", "parkingcrew.net", "above.com",
        "domainsherpa.com", "domcop.com", "flippa.com", "atom.com",
        "register.com", "networksolutions.com", "web.com", "bluehost.com",
        "hostgator.com", "ionos.com", "hover.com", "porkbun.com",
        "enom.com", "1and1.com", "domainnameshop.com",
    }

    # ── Registrar URL path patterns ────────────────────────────────────────
    REGISTRAR_URL_PATTERNS = (
        "/domainsearch", "/domain-search", "/domain-for-sale",
        "/domains/search", "/find-domain", "/buy-domain",
        "domainfind", "domaincheck", "whois-lookup",
        "/register-domain", "/domain-registration",
    )

    # ── Page content signals (in first 4KB of body) ────────────────────────
    PARKED_CONTENT_SIGNALS = (
        # Generic for-sale phrases
        "domain for sale", "buy this domain", "this domain is for sale",
        "purchase this domain", "acquire this domain", "make an offer",
        "domain may be for sale", "domain is available",
        # Parking service phrases
        "parked by", "domain parking", "parking page", "parked domain",
        "this page is parked", "this web page is parked",
        # Specific registrar/marketplace phrases
        "sedoparking", "sedo.com", "hugedomains.com", "afternic.com",
        "dan.com", "undeveloped.com", "efty.com", "squadhelp.com",
        "godaddy.com/domainsearch", "namecheap.com/domains",
        "register this domain", "register your domain",
        "get this domain", "grab this domain",
        "interested in this domain", "inquire about this domain",
        "buy now", "make offer",  # combined with domain context below
        "parkingcrew", "bodis.com", "above.com",
        # Expired / not yet launched
        "this domain has expired", "domain expired",
        "coming soon", "website coming soon", "under construction",
        "this site is under construction",
    )

    def _is_registrar_url(url: str) -> bool:
        """Check if the final URL landed on a registrar or parking host/path."""
        url_lower = url.lower()
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_lower)
            host = parsed.netloc.lstrip("www.")
            # Check if the host itself is a registrar
            if any(host == r or host.endswith("." + r) for r in REGISTRAR_HOSTS):
                return True
            # Check if path contains registrar purchase patterns
            if any(pattern in parsed.path or pattern in url_lower for pattern in REGISTRAR_URL_PATTERNS):
                return True
        except Exception:
            pass
        return False

    async with semaphore:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        for scheme in ["https://", "http://"]:
            url = f"{scheme}{domain}"
            try:
                async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    server_header = resp.headers.get("Server", "").lower()
                    has_cf_header = (
                        "cloudflare" in server_header
                        or "cf-ray" in resp.headers
                        or "cf-cache-status" in resp.headers
                    )

                    # Cloudflare block
                    if resp.status in (403, 429, 503) and (
                        has_cf_header or "cloudflare" in final_url.lower()
                    ):
                        return domain, "blocked"

                    # ── Check if redirect landed on a registrar/parking page ──
                    if _is_registrar_url(final_url):
                        return domain, False

                    # Only 2xx = genuinely active
                    if 200 <= resp.status < 300:
                        # Read first 4KB to check for parked/for-sale content
                        try:
                            body = (await resp.content.read(4096)).decode("utf-8", errors="ignore").lower()
                            if any(signal in body for signal in PARKED_CONTENT_SIGNALS):
                                return domain, False  # parked or for-sale page
                        except Exception:
                            pass
                        return domain, True

                    # 4xx / 5xx = inactive
                    return domain, False

            except aiohttp.ClientResponseError as e:
                if e.status in (403, 429, 503):
                    return domain, "blocked"
            except Exception:
                pass
        return domain, False


async def evaluate_domains_status(
    domains: list[str], max_workers: int = 200, timeout_sec: float = 5.0, batch_label: str = ""
) -> list[tuple[str, bool | str]]:
    """Evaluate DNS resolution and HTTP status for domains in current batch."""
    if not domains:
        return []

    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(max_workers)

    # Stage 1: DNS Resolution
    dns_tasks = [check_dns(d, loop) for d in domains]
    dns_results = {}

    with tqdm(
        total=len(domains),
        desc=f"  [{batch_label}] DNS Check ",
        unit="dom",
        ncols=90,
        leave=False,
    ) as pbar:
        for future in asyncio.as_completed(dns_tasks):
            domain, resolved = await future
            dns_results[domain] = resolved
            pbar.update(1)

    resolvable_domains = [d for d in domains if dns_results[d]]
    results = [(d, False) for d in domains if not dns_results[d]]

    # Stage 2: HTTP/HTTPS probe
    if resolvable_domains:
        connector = aiohttp.TCPConnector(limit=max_workers, ssl=False)
        async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
            http_tasks = [
                check_domain_http(d, session, semaphore, timeout_sec)
                for d in resolvable_domains
            ]

            with tqdm(
                total=len(http_tasks),
                desc=f"  [{batch_label}] HTTP Probe",
                unit="dom",
                ncols=90,
                leave=False,
            ) as pbar:
                for future in asyncio.as_completed(http_tasks):
                    domain, status = await future
                    results.append((domain, status))
                    pbar.update(1)

    return results


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    config = get_config()

    parser = argparse.ArgumentParser(
        description=(
            "Extract domains from Common Crawl in streaming batches, "
            "evaluate status, and write immediately to MongoDB."
        )
    )
    parser.add_argument(
        "--keyword", "--keywords",
        nargs="+",
        required=True,
        help="One or more keywords to search for (e.g. --keyword bet casino).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=config["max_workers"],
        help=f"Max concurrent worker connections (default: {config['max_workers']}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=config["timeout"],
        help=f"HTTP probe timeout in seconds (default: {config['timeout']}).",
    )
    parser.add_argument(
        "--batch-files",
        type=int,
        default=config["file_batch_size"],
        help=f"Number of Common Crawl parquet files per streaming batch (default: {config['file_batch_size']}).",
    )
    parser.add_argument(
        "--no-mongo",
        action="store_true",
        help="Disable export to MongoDB even if EXPORT_TO_MONGO=true in .env.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint if available (default: auto-resume when keywords match).",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing checkpoint and start fresh.",
    )

    args = parser.parse_args()

    # Normalize & deduplicate input keywords
    keywords = []
    for item in args.keyword:
        for kw in item.split(","):
            kw_clean = kw.strip().lower()
            if kw_clean and kw_clean not in keywords:
                keywords.append(kw_clean)

    files = get_parquet_files()
    total_files = len(files)
    file_batch_size = args.batch_files

    file_chunks = [files[i:i + file_batch_size] for i in range(0, total_files, file_batch_size)]
    total_batches = len(file_chunks)

    # ── Checkpoint / Resume ────────────────────────────────────────────────
    start_batch = 0
    seen_domains_session: set[str] = set()
    total_extracted_all = 0
    total_written_all = 0

    if not args.no_resume:
        checkpoint = load_checkpoint()
        ckpt_keywords = checkpoint.get("keywords", [])
        if checkpoint and ckpt_keywords == keywords:
            start_batch = checkpoint.get("last_completed_batch", 0)
            seen_domains_session = set(checkpoint.get("seen_domains", []))
            total_extracted_all = checkpoint.get("total_extracted", 0)
            total_written_all = checkpoint.get("total_written", 0)
            if start_batch > 0:
                print(
                    f"\n[Checkpoint] Resuming from Batch {start_batch + 1}/{total_batches}  "
                    f"({len(seen_domains_session):,} domains already seen)\n"
                )
        elif checkpoint and ckpt_keywords != keywords:
            print("[Checkpoint] Keywords changed — ignoring old checkpoint and starting fresh.\n")

    # ── Header ─────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"Streaming Pipeline Target Keywords : {', '.join(keywords)}")
    print(f"Total Parquet Files : {total_files}  |  Batch size: {file_batch_size} files")
    print(f"Total Batches       : {total_batches}  |  Starting at batch: {start_batch + 1}")
    print("RAM Management      : Streaming mode enabled (freed after every batch)")
    print("=" * 70)

    mongo_client = get_mongo_client() if not args.no_mongo else None
    if not args.no_mongo and mongo_client:
        print(f"Connected to MongoDB  DB: '{config['db_name']}'  Collection: '{config['collection']}'\n")

    like_clauses = " OR ".join(["LOWER(url_host_registered_domain) LIKE ?" for _ in keywords])
    params = [f"%{kw}%" for kw in keywords]

    start_all_time = time.time()
    chunks_to_run = list(enumerate(file_chunks[start_batch:], start=start_batch + 1))
    remaining = len(chunks_to_run)

    # ── Overall progress bar ────────────────────────────────────────────────
    overall_pbar = tqdm(
        total=remaining,
        desc="Overall Progress",
        unit="batch",
        ncols=90,
        colour="green",
        position=0,
    )

    # ── Thread pool for parallel parquet prefetch ───────────────────────────
    executor = ThreadPoolExecutor(max_workers=1)
    prefetch_future = None

    for idx, (batch_num, batch_files) in enumerate(chunks_to_run):
        batch_label = f"Batch {batch_num}/{total_batches}"
        overall_pbar.set_description(f"Overall [{batch_label}]")

        print(f"\n{'─' * 70}")
        print(f"[{batch_label}] Processing {len(batch_files)} Parquet files")
        print(f"{'─' * 70}")

        # Use prefetched result if ready, otherwise query now
        if prefetch_future is not None:
            result = prefetch_future.result()
        else:
            result = query_parquet_batch(batch_files, like_clauses, params, batch_label)

        # Prefetch NEXT batch in background while we do DNS/HTTP on this one
        next_idx = idx + 1
        if next_idx < len(chunks_to_run):
            next_batch_num, next_batch_files = chunks_to_run[next_idx]
            next_label = f"Batch {next_batch_num}/{total_batches}"
            prefetch_future = executor.submit(
                query_parquet_batch, next_batch_files, like_clauses, params, next_label
            )
        else:
            prefetch_future = None

        # ── Deduplicate ─────────────────────────────────────────────────────
        extracted_domains = []
        for (domain,) in result:
            if domain:
                d_clean = domain.strip().lower()
                if d_clean and d_clean not in seen_domains_session:
                    seen_domains_session.add(d_clean)
                    extracted_domains.append(d_clean)

        total_extracted_all += len(extracted_domains)
        print(
            f"  -> Extracted {len(extracted_domains):,} new domains  "
            f"(Session unique total: {len(seen_domains_session):,})"
        )

        if not extracted_domains:
            print("  -> No new domains in this batch. Moving to next batch...")
            save_checkpoint({
                "keywords": keywords,
                "last_completed_batch": batch_num,
                "seen_domains": list(seen_domains_session),
                "total_extracted": total_extracted_all,
                "total_written": total_written_all,
            })
            overall_pbar.update(1)
            continue

        # ── DNS + HTTP evaluation ───────────────────────────────────────────
        domain_results = asyncio.run(
            evaluate_domains_status(
                extracted_domains,
                max_workers=args.workers,
                timeout_sec=args.timeout,
                batch_label=batch_label,
            )
        )

        active_b = sum(1 for _, s in domain_results if s is True)
        inactive_b = sum(1 for _, s in domain_results if s is False)
        blocked_b = sum(1 for _, s in domain_results if s == "blocked")

        print(
            f"  -> Status: {active_b:,} active  |  {inactive_b:,} inactive  |  {blocked_b:,} blocked"
        )

        # ── MongoDB write ───────────────────────────────────────────────────
        if mongo_client:
            written_b = export_domains_to_mongo(domain_results, mongo_client=mongo_client)
            total_written_all += written_b
            print(
                f"  -> Streamed {written_b:,} documents to MongoDB  "
                f"(Session total written: {total_written_all:,})"
            )

        # ── Save checkpoint ─────────────────────────────────────────────────
        save_checkpoint({
            "keywords": keywords,
            "last_completed_batch": batch_num,
            "seen_domains": list(seen_domains_session),
            "total_extracted": total_extracted_all,
            "total_written": total_written_all,
        })

        # ── Free RAM ────────────────────────────────────────────────────────
        del extracted_domains
        del domain_results
        gc.collect()

        overall_pbar.update(1)

    overall_pbar.close()
    executor.shutdown(wait=False)

    if mongo_client:
        mongo_client.close()

    # Delete checkpoint on clean full completion
    clear_checkpoint()

    elapsed = time.time() - start_all_time
    print("\n" + "=" * 70)
    print(f"STREAMING PIPELINE COMPLETED in {elapsed / 60:.2f} minutes!")
    print(f"  Total Unique Domains Extracted : {len(seen_domains_session):,}")
    print(f"  Total Documents in MongoDB     : {total_written_all:,}")
    print("=" * 70)


if __name__ == "__main__":
    main()