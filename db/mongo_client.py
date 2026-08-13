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
from pymongo import MongoClient, ASCENDING
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
        _ensure_indexes()
    return _db


def _ensure_indexes():
    checked_domains().create_index([("status", ASCENDING), ("checked_at", ASCENDING)])


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
    """Yield active domains not yet in checked_domains."""
    already_checked = set(d["_id"] for d in checked_domains().find({}, {"_id": 1}))
    cur = source_domains().find({"active": True})
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        if doc.get("domain") and doc["domain"] not in already_checked:
            yield doc


# ── Result writer ─────────────────────────────────────────────────────────────

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def write_result(domain: str, *, status: str, reason: list, url: str = None):
    """Upsert a classification result into checked_domains."""
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    today_date = datetime.now(IST).strftime("%Y-%m-%d")
    doc = {
        "_id": domain,
        "domain": domain,
        "url": url or f"https://{domain}",
        "status": status,
        "reason": reason or [],
        "checked_at": now_ist,
        "screenshot_taken": False,
        "screenshot_failed_reason": None,
    }
    checked_domains().update_one(
        {"_id": domain},
        {
            "$set": doc,
            "$setOnInsert": {"added_date": today_date},
        },
        upsert=True,
    )


def get_checked(domain: str) -> dict | None:
    return checked_domains().find_one({"_id": domain})


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
