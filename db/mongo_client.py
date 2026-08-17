"""
db/mongo_client.py — MongoDB connection, collection accessors, domain helpers.

All config from root .env via python-dotenv.

Two-collection flow:
  source:  domain_Listed  — existing domains (active=True only)
  dest:    checked_domains — classification results written here
"""
import csv
import os
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

import tldextract
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection

# Load root .env
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# ── Connection ────────────────────────────────────────────────────────────────

_client: MongoClient | None = None
_db = None


def get_db():
    global _client, _db
    if _client is None:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        db_name = os.getenv("MONGO_DB_NAME", "gamblingsites")
        _client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=30000,
            maxPoolSize=100,
            minPoolSize=5,
            retryWrites=True,
        )
        _db = _client[db_name]
        try:
            _client.admin.command('ping')
        except Exception:
            # Self-healing: auto-start MongoDB daemon if offline
            import subprocess
            import time
            mongod_path = r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe"
            mongod_cfg = r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.cfg"
            if os.path.exists(mongod_path) and os.path.exists(mongod_cfg):
                try:
                    subprocess.Popen([mongod_path, "--config", mongod_cfg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(2)
                except Exception:
                    pass
            _client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000, socketTimeoutMS=30000)
            _db = _client[db_name]
    return _db


def source_domains() -> Collection:
    """domain_Listed — READ ONLY. Source of domains to process."""
    get_db()
    return _db[os.getenv("MONGO_COLLECTION", "domain_Listed")]


def checked_domains() -> Collection:
    """checked_domains — results written here."""
    get_db()
    return _db[os.getenv("CHECKED_COLLECTION", "checked_domains")]


# ── URL helpers ───────────────────────────────────────────────────────────────

def normalize_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(raw_url.strip())
    scheme = parsed.scheme.lower() or 'http'
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    if ':' in netloc:
        host, port = netloc.split(':', 1)
        if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
            netloc = host
    path = parsed.path.rstrip('/') if len(parsed.path) > 1 else parsed.path
    return urllib.parse.urlunparse((scheme, netloc, path, parsed.params, parsed.query, ''))


def extract_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return (ext.registered_domain or ext.domain).lower()


def extract_domains_from_file(file_path: str) -> list[str]:
    """Read domains/URLs from an Excel (.xlsx/.xls), CSV, or text file.

    Robust against encoding issues, headers, multiple columns, and URLs.
    Returns a deduplicated list of valid registered domains in order.
    """
    path = Path(file_path)
    if not path.exists():
        return []

    domains = []
    seen = set()

    def _add_cand(raw: any):
        if not raw:
            return
        text = str(raw).strip()
        if not text or text.startswith("#"):
            return
        text = text.strip('"\' ,;')
        token = text.split(",")[0].split()[0].strip()
        token = token.replace("https://", "").replace("http://", "").lstrip("www.").rstrip("/").lower()
        d = extract_domain(f"https://{token}") or token
        if d and "." in d and d not in seen:
            seen.add(d)
            domains.append(d)

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for val in row:
                        if val is not None and "." in str(val):
                            _add_cand(str(val))
            wb.close()
        except Exception:
            try:
                import pandas as pd
                df = pd.read_excel(file_path)
                for col in df.columns:
                    for val in df[col].dropna():
                        if "." in str(val):
                            _add_cand(str(val))
            except Exception as e:
                print(f"[extract_domains_from_file] Excel read error: {e}")
    else:
        encodings = ("utf-8-sig", "utf-8", "latin-1", "cp1252")
        lines = []
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    lines = f.readlines()
                break
            except Exception:
                continue

        for raw in lines:
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = [p.strip() for p in raw.replace("\t", ",").split(",") if p.strip()]
            for p in parts:
                if "." in p:
                    _add_cand(p)

    return domains


# ── Source collection helpers ─────────────────────────────────────────────────

def find_active_domains(limit: int = 0):
    """Yield active domains not yet processed by checking_url.

    Filters out documents where processed=True so that re-runs never
    re-check the same domain twice.  Older documents without the
    'processed' field are treated as unprocessed (backward-compatible).
    """
    cur = source_domains().find(
        {
            "active": True,
            # Include docs with no 'processed' field (legacy) OR processed=False
            "processed": {"$ne": True},
        }
    )
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        if doc.get("domain"):
            yield doc


def find_blocked_domains(limit: int = 0):
    """Yield domains previously marked as blocked in checked_domains or domain_Listed."""
    seen = set()
    cur_checked = checked_domains().find({"status": "blocked"})
    if limit:
        cur_checked = cur_checked.limit(limit)
    for doc in cur_checked:
        domain = doc.get("domain") or doc.get("_id")
        if domain and domain not in seen:
            seen.add(domain)
            yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}
            if limit and len(seen) >= limit:
                return

    if not limit or len(seen) < limit:
        cur_source = source_domains().find({"active": "blocked"})
        if limit:
            cur_source = cur_source.limit(limit - len(seen))
        for doc in cur_source:
            domain = doc.get("domain") or doc.get("_id")
            if domain and domain not in seen:
                seen.add(domain)
                yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}
                if limit and len(seen) >= limit:
                    return


def find_unconfirmed_domains(limit: int = 0):
    """Yield domains previously marked as unconfirmed in checked_domains."""
    seen = set()
    cur_checked = checked_domains().find({"status": "unconfirmed"})
    if limit:
        cur_checked = cur_checked.limit(limit)
    for doc in cur_checked:
        domain = doc.get("domain") or doc.get("_id")
        if domain and domain not in seen:
            seen.add(domain)
            yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}
            if limit and len(seen) >= limit:
                return


# ── Result writer ─────────────────────────────────────────────────────────────

def write_result(
    domain: str,
    *,
    status: str,
    reason: str | list,
    url: str = None,
    screenshot_taken: bool | None = None,
    screenshot_failed_reason: str | None = None,
):
    """Upsert a classification result into checked_domains AND sync active/processed
    in domain_Listed.

    Statuses handled:
      - 'gambling': live verified gambling site (+ immediate screenshot flag)
      - 'regular': non-gambling site
      - 'unconfirmed': Ollama offline/timeout or network error (processed=False to re-run!)
      - 'blocked': 403 / Cloudflare WAF
      - 'dead': 404 / Connection failed / Parked lander
    """
    today_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Format reason as concise short-form text
    if isinstance(reason, list):
        if len(reason) == 0:
            formatted_reason = "No reason provided"
        elif len(reason) == 1:
            formatted_reason = str(reason[0])
        else:
            formatted_reason = ", ".join(str(r) for r in reason)
    else:
        formatted_reason = str(reason) if reason else "No reason provided"

    set_fields = {
        "domain": domain,
        "url": url or f"https://{domain}",
        "status": status,
        "reason": formatted_reason,
    }


    if screenshot_taken is not None:
        set_fields["screenshot_taken"] = bool(screenshot_taken)
        set_fields["screenshot_failed_reason"] = screenshot_failed_reason

    update = {"$set": set_fields}

    on_insert_fields = {"added_date": today_date}

    if status == "gambling" and screenshot_taken is None:
        on_insert_fields["screenshot_taken"] = False
        on_insert_fields["screenshot_failed_reason"] = None

    update["$setOnInsert"] = on_insert_fields

    import time
    for attempt in range(3):
        try:
            checked_domains().update_one(
                {"_id": domain},
                update,
                upsert=True,
            )

            # Sync status in source domain_Listed
            # If unconfirmed (Ollama was down), leave processed=False so it gets re-run!
            if status == "unconfirmed":
                source_domains().update_one(
                    {"_id": domain},
                    {"$set": {"processed": False, "active": True}},
                )
            elif status == "blocked":
                source_domains().update_one(
                    {"_id": domain},
                    {"$set": {"processed": True, "active": "blocked"}},
                )
            elif status == "dead":
                source_domains().update_one(
                    {"_id": domain},
                    {"$set": {"processed": True, "active": False}},
                )
            else:  # gambling or regular
                source_domains().update_one(
                    {"_id": domain},
                    {"$set": {"processed": True, "active": True}},
                )
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                print(f"[write_result WARNING] Failed writing to MongoDB for domain '{domain}': {e}")


def get_checked(domain: str) -> dict | None:
    return checked_domains().find_one({"_id": domain})


def find_pending_capture(limit: int = 0):
    """Gambling domains not yet screenshotted."""
    cur = checked_domains().find({"status": "gambling", "screenshot_taken": False})
    if limit:
        cur = cur.limit(limit)
    return cur


def find_unexported_gambling_domains(limit: int = 0):
    """Gambling domains that have valid screenshots captured and are pending report export."""
    query = {
        "status": "gambling",
        "screenshot_taken": True,
        "exported": {"$ne": True},
    }
    cur = checked_domains().find(query)
    if limit:
        cur = cur.limit(limit)
    return cur


def mark_domains_exported(domain_ids: list[str]):
    """Mark domain list as exported with timestamp."""
    if not domain_ids:
        return
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    checked_domains().update_many(
        {"_id": {"$in": domain_ids}},
        {"$set": {"exported": True, "exported_at": now_ts}},
    )


# ── CSV seeder ────────────────────────────────────────────────────────────────

def seed_from_csv(path: str, active: bool = True):
    """Import domains from CSV into domain_Listed.

    ponytail: ceiling = no progress bar; upgrade = tqdm if > 10k rows.
    """
    inserted = skipped = 0
    today_date = datetime.now(IST).strftime("%Y-%m-%d")
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            domain = (row.get('domain') or row.get('url') or '').strip()
            if not domain:
                skipped += 1
                continue
            domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
            source_domains().update_one(
                {"_id": domain},
                {
                    "$set": {"_id": domain, "domain": domain, "active": active},
                    "$setOnInsert": {"added_date": today_date},
                },
                upsert=True,
            )
            inserted += 1
    print(f"[seed] {inserted} upserted into domain_Listed, {skipped} skipped")


def ingest_true_positives(path: str) -> tuple[int, int]:
    """Import confirmed gambling domains from an Excel (.xlsx/.xls), CSV, or .txt file directly into
    checked_domains as status='gambling', screenshot_taken=False.

    Skips domains already present in checked_domains.
    Returns (inserted, skipped).
    """
    inserted = skipped = 0
    today_date = datetime.now(IST).strftime("%Y-%m-%d")

    domains = extract_domains_from_file(path)
    for domain in domains:
        if not domain or "." not in domain:
            skipped += 1
            continue

        # If already exists, reset for re-capture and re-export
        existing = checked_domains().find_one({"_id": domain}, {"_id": 1})
        if existing:
            checked_domains().update_one(
                {"_id": domain},
                {"$set": {"screenshot_taken": False, "exported": False, "screenshot_failed_reason": None}},
            )
            skipped += 1
            continue

        # Insert directly as confirmed gambling true positive
        checked_domains().update_one(
            {"_id": domain},
            {
                "$set": {
                    "domain": domain,
                    "url": f"https://{domain}",
                    "status": "gambling",
                    "reason": "Manual true positive import",
                    "screenshot_taken": False,
                    "screenshot_failed_reason": None,
                    "source": "manual_import",
                },
                "$setOnInsert": {"added_date": today_date},
            },
            upsert=True,
        )
        # Also mark processed in source collection
        source_domains().update_one(
            {"_id": domain},
            {"$set": {"active": True, "processed": True}},
            upsert=False,
        )
        inserted += 1

    return inserted, skipped


def seed_discovered_domains(domains: set, discovered_from: str) -> tuple[int, int]:

    """Bulk upsert domains discovered via deep crawl into domain_Listed.

    Only inserts domains NOT already present (uses $setOnInsert so existing
    records are never overwritten). Newly discovered domains are marked
    processed: False so they get picked up by future runs.
    """
    if not domains:
        return 0, 0

    today_date = datetime.now(IST).strftime("%Y-%m-%d")
    inserted = 0
    skipped = 0

    for domain in domains:
        domain = domain.strip().lower().lstrip("www.").rstrip("/")
        if not domain or "." not in domain:
            skipped += 1
            continue
        try:
            result = source_domains().update_one(
                {"_id": domain},
                {
                    "$setOnInsert": {
                        "_id": domain,
                        "domain": domain,
                        "active": True,
                        "processed": False,
                        "added_date": today_date,
                        "source": "deep_crawl",
                        "discovered_from": discovered_from,
                    }
                },
                upsert=True,
            )
            if result.upserted_id is not None:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    return inserted, skipped


