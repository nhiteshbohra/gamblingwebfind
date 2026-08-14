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
from datetime import datetime, timezone
from pathlib import Path

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
        _client = MongoClient(uri)
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


def keywords_col() -> Collection:
    """keywords collection — gambling signal terms loaded by classifier."""
    get_db()
    return _db[os.getenv("KEYWORDS_COLLECTION", "keywords")]


# ── URL helpers ───────────────────────────────────────────────────────────────

_TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'msockid', 'msclkid', 'ref', 'ref_id', 'aff_id',
    'session_id', 'clickid', 'affid', 'btag', 'tag', 'subid', 'cid'
}


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
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    params = sorted((k, v) for k, v in params if k.lower() not in _TRACKING_PARAMS)
    return urllib.parse.urlunparse((scheme, netloc, path, parsed.params, urllib.parse.urlencode(params), ''))


def extract_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return (ext.registered_domain or ext.domain).lower()


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


def find_gambling_domains_by_date(added_date: str, limit: int = 0):
    """Yield domains currently marked as gambling with the specified added_date."""
    query = {"status": "gambling", "added_date": added_date}
    cur = checked_domains().find(query)
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        domain = doc.get("domain") or doc.get("_id")
        if domain:
            yield {
                "domain": domain,
                "_id": domain,
                "url": doc.get("url", f"https://{domain}"),
                "added_date": doc.get("added_date"),
            }


# ── Result writer ─────────────────────────────────────────────────────────────

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def write_result(domain: str, *, status: str, reason: list, url: str = None):
    """Upsert a classification result into checked_domains AND mark the
    source domain as processed in domain_Listed.

    Both writes happen in the same function call — eliminates the crash
    window that existed when mark_domain_processed() was a separate call
    in runner.py after write_result().

    On re-check (domain already exists), screenshot_taken is preserved so
    a previously-captured screenshot is not silently discarded.
    """
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    today_date = datetime.now(IST).strftime("%Y-%m-%d")

    # Always overwrite classification fields and added_date (so re-classified domains
    # get today's date — the aggregation by added_date stays accurate).
    # screenshot_taken / screenshot_failed_reason are only set on first insert.
    checked_domains().update_one(
        {"_id": domain},
        {
            "$set": {
                "domain": domain,
                "url": url or f"https://{domain}",
                "status": status,
                "reason": reason or [],
                "checked_at": now_ist,
                "added_date": today_date,
            },
            "$setOnInsert": {
                "screenshot_taken": False,
                "screenshot_failed_reason": None,
            },
        },
        upsert=True,
    )

    # Sync active status in domain_Listed to match new classification.
    # gambling / regular  ->  active = True   (site is live and classified)
    # blocked             ->  active = "blocked"
    # dead                ->  active = False
    active_val = "blocked" if status == "blocked" else (False if status == "dead" else True)
    source_domains().update_one(
        {"_id": domain},
        {"$set": {"processed": True, "active": active_val}},
    )


def get_checked(domain: str) -> dict | None:
    return checked_domains().find_one({"_id": domain})


def mark_domain_processed(domain: str):
    """Flip processed=True on the source domain_Listed document.

    Called by checking_url/runner.py after write_result() so that
    re-runs of the checker skip this domain entirely.
    """
    source_domains().update_one(
        {"_id": domain},
        {"$set": {"processed": True}},
    )


def find_pending_capture(limit: int = 0):
    """Gambling domains not yet screenshotted."""
    cur = checked_domains().find({"status": "gambling", "screenshot_taken": False})
    if limit:
        cur = cur.limit(limit)
    return cur


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
