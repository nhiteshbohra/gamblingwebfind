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

from typing import Any
import socket
import tldextract
from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection

# Load root .env
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def resolve_ip(domain: str) -> str | list[str] | None:
    """Fast DNS resolver for a domain name. Returns list if multiple IPs, string if single."""
    try:
        clean = domain.removeprefix("https://").removeprefix("http://").split("/")[0].split(":")[0].strip()
        if not clean:
            return None
        # Try dnspython first to capture all A records
        try:
            import dns.resolver
            res = dns.resolver.Resolver()
            res.nameservers = ["8.8.8.8", "1.1.1.1"]
            res.timeout = 2.0
            res.lifetime = 2.5
            answers = res.resolve(clean, "A")
            ips = list(dict.fromkeys([str(r.address) for r in answers]))
            if len(ips) > 1:
                return ips
            elif len(ips) == 1:
                return ips[0]
        except Exception:
            pass

        # Fallback to socket getaddrinfo
        addrinfo = socket.getaddrinfo(clean, None, socket.AF_INET)
        ips = list(dict.fromkeys([item[4][0] for item in addrinfo if item and item[4]]))
        if len(ips) > 1:
            return ips
        elif len(ips) == 1:
            return ips[0]
        return None
    except Exception:
        return None


def resolve_asn(ip: str) -> str | None:
    """Look up the hosting ASN for an IP via Team Cymru's free DNS-based WHOIS service
    (no API key, no local GeoIP/MaxMind database to download/maintain — just a DNS TXT
    query, which fits a resource-constrained machine and a 1M-domain batch run).

    Used for hosting-cluster corroboration: illegal betting operators frequently reuse the
    same bulletproof/offshore hosting providers and mirror templates across many domains, so
    "this new domain's ASN already has a heavy concentration of confirmed gambling domains"
    is a useful secondary signal. See get_gambling_asn_concentration().
    """
    if not ip or ":" in ip:  # skip IPv6 for the Cymru v4 origin service
        return None
    try:
        import dns.resolver
        octets = ip.strip().split(".")
        if len(octets) != 4 or not all(o.isdigit() for o in octets):
            return None
        reversed_ip = ".".join(reversed(octets))
        res = dns.resolver.Resolver()
        res.nameservers = ["8.8.8.8", "1.1.1.1"]
        res.timeout = 2.0
        res.lifetime = 2.5
        answers = res.resolve(f"{reversed_ip}.origin.asn.cymru.com", "TXT")
        # Response format: "ASN | IP_PREFIX | COUNTRY | REGISTRY | ALLOCATED_DATE"
        txt = str(answers[0]).strip('"')
        asn_part = txt.split("|")[0].strip()
        return f"AS{asn_part}" if asn_part.isdigit() else None
    except Exception:
        return None


def get_gambling_asn_concentration(min_sample: int = 5, min_ratio: float = 0.7) -> dict:
    """Return {asn: gambling_ratio} for ASNs that are heavily gambling-concentrated in what
    we've already classified — at least `min_sample` domains observed on that ASN AND at
    least `min_ratio` of them confirmed gambling.

    This is deliberately conservative: big shared clouds/CDNs (AWS, Cloudflare, Azure,
    DigitalOcean, OVH...) host huge numbers of BOTH gambling and completely unrelated
    legitimate sites, so their ratio will essentially never cross a high threshold like 0.7 —
    only small, dedicated hosting clusters that illegal operators reuse across many mirror
    domains will qualify. This is used as a soft corroborating signal for already-ambiguous
    (needs_ai) domains, never as a standalone auto-gambling trigger — shared hosting alone is
    not proof, and treating it as one would reintroduce the exact kind of unreviewed
    false-positive lock this project already got burned by once.
    """
    try:
        pipeline = [
            {"$match": {"asn": {"$exists": True, "$ne": None}, "status": {"$in": ["gambling", "regular"]}}},
            {"$group": {
                "_id": "$asn",
                "total": {"$sum": 1},
                "gambling": {"$sum": {"$cond": [{"$eq": ["$status", "gambling"]}, 1, 0]}},
            }},
            {"$match": {"total": {"$gte": min_sample}}},
        ]
        result = {}
        for doc in checked_domains().aggregate(pipeline):
            ratio = doc["gambling"] / doc["total"]
            if ratio >= min_ratio and doc["_id"]:
                result[doc["_id"]] = round(ratio, 2)
        return result
    except Exception as e:
        print(f"[get_gambling_asn_concentration WARNING] {e}")
        return {}

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
            # Self-healing: auto-start MongoDB daemon if configured and offline
            import subprocess
            import time
            mongod_path = os.getenv("MONGOD_AUTOSTART_PATH", r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe")
            mongod_cfg = os.getenv("MONGOD_AUTOSTART_CFG", r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.cfg")
            if mongod_path and mongod_cfg and os.path.exists(mongod_path) and os.path.exists(mongod_cfg):
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

    def _add_cand(raw: Any):
        if not raw:
            return
        text = str(raw).strip()
        if not text or text.startswith("#"):
            return
        text = text.strip('"\' ,;')
        token = text.split(",")[0].split()[0].strip()
        token = token.replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/").lower()
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
        cur_source = source_domains().find({"$or": [{"block_reason": "blocked"}, {"active": "blocked"}]})
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


def find_regular_domains(limit: int = 0, min_age_days: int = 0):
    """Yield domains previously marked as regular in checked_domains."""
    flt = {"status": "regular"}
    if min_age_days > 0:
        cutoff = (datetime.now(IST) - timedelta(days=min_age_days)).strftime("%Y-%m-%d")
        flt["$or"] = [{"last_checked_at": {"$lt": cutoff}}, {"last_checked_at": {"$exists": False}}]
    cur = checked_domains().find(flt)
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        domain = doc.get("domain") or doc.get("_id")
        if domain:
            yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}


def find_dead_domains(limit: int = 0, min_age_days: int = 0):
    """Yield domains previously marked as dead in checked_domains or source_domains."""
    seen = set()
    flt = {"status": "dead"}
    if min_age_days > 0:
        cutoff = (datetime.now(IST) - timedelta(days=min_age_days)).strftime("%Y-%m-%d")
        flt["$or"] = [{"last_checked_at": {"$lt": cutoff}}, {"last_checked_at": {"$exists": False}}]
    cur_checked = checked_domains().find(flt)
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
        cur_source = source_domains().find({"active": False, "block_reason": {"$exists": False}})
        if limit:
            cur_source = cur_source.limit(limit - len(seen))
        for doc in cur_source:
            domain = doc.get("domain") or doc.get("_id")
            if domain and domain not in seen:
                seen.add(domain)
                yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}
                if limit and len(seen) >= limit:
                    return


def find_for_sale_domains(limit: int = 0):
    """Yield domains previously marked as for_sale (parked/registrar lander) in
    checked_domains -- a parked domain can get bought and turned into a live site
    (gambling or otherwise) later, so it's worth periodic recheck same as dead/blocked."""
    flt = {"status": "for_sale"}
    cur = checked_domains().find(flt)
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        domain = doc.get("domain") or doc.get("_id")
        if domain:
            yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}


def find_exported_gambling_domains(limit: int = 0):
    """Yield exported (reported for blocking) domains worth re-checking -- either still
    'gambling' (never found down yet) or already 'reported_down' (re-checked in
    case it's come back online, in which case it reverts to 'gambling') -- the candidate
    set for reported_blocked_checker.py."""
    flt = {"status": {"$in": ["gambling", "reported_down"]}, "exported": True}
    cur = checked_domains().find(flt)
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        domain = doc.get("domain") or doc.get("_id")
        if domain:
            yield {
                "domain": domain,
                "_id": domain,
                "url": doc.get("url", f"https://{domain}"),
                "ip": doc.get("ip"),
                "status": doc.get("status"),
            }


# ── Result writer ─────────────────────────────────────────────────────────────

def write_result(
    domain: str,
    *,
    status: str,
    reason: str | list,
    url: str = None,
    screenshot_taken: bool | None = None,
    screenshot_failed_reason: str | None = None,
    ip: str | None = None,
    asn: str | None = None,
    confidence: float | None = None,
    category: str | None = None,
    decided_by: str | None = None,
    ai_context: dict | None = None,
    matched_keywords: list | None = None,
):
    """Upsert a classification result into checked_domains AND sync active/processed
    in domain_Listed.

    Statuses handled:
      - 'gambling': live verified gambling site (+ immediate screenshot flag)
      - 'regular': non-gambling site
      - 'unconfirmed': Ollama offline/timeout or network error (processed=False to re-run!)
      - 'blocked': 403 / Cloudflare WAF
      - 'dead': 404 confirmed / Connection failed (no live content, no parking lander)
      - 'for_sale': domain-for-sale / registrar parking lander — reachable and rendering
        real content, just not gambling and not dead, and not a real site of its own
        either, so it gets its own status distinct from both 'regular' and 'dead'.
      - 'reported_down': an exported/reported 'gambling' domain that
        reported_blocked_checker.py found unreachable — the expected outcome once an
        ISP/regulator acts on the report. Kept distinct from 'dead' (which means something
        unrelated: never-was-reachable) so a reported-and-now-down domain stays
        identifiable. Found reachable again on a later recheck, it reverts straight back to
        'gambling'. Its screenshot is untouched either way — see delete_screenshot()'s
        docstring for why.

    confidence/category/decided_by persist the AI's own self-reported certainty and which
    pipeline stage produced the verdict (e.g. "heuristic_score", "domain_anchor_strong",
    "ai_round1", "ai_round2_challenge", "validator_confirmed", "validator_override") so a
    review queue can be built afterwards (e.g. "every gambling verdict with confidence < 0.75
    or decided_by == heuristic_score") without re-running anything. Previously these were
    computed by classify_with_challenge() but discarded at write time — with a 1M-domain
    irreversible-ban pipeline, throwing away the model's own certainty made it impossible to
    triage which verdicts most need a human's eyes before acting on them.

    matched_keywords persists the actual heuristic keyword/signal list (not just its count,
    which was already folded into `reason`'s text) so the dashboard's domain inspector can
    show exactly which terms triggered a verdict, not just how many.
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

    if not ip:
        ip = resolve_ip(domain)
    # ASN is only useful for the gambling/regular concentration signal (see
    # get_gambling_asn_concentration) — skip the extra DNS round trip for
    # dead/blocked/unconfirmed writes where it would never be queried anyway.
    if ip and not asn and status in ("gambling", "regular"):
        try:
            single_ip = ip[0] if isinstance(ip, list) else ip
            asn = resolve_asn(single_ip)
        except Exception:
            asn = None

    set_fields = {
        "domain": domain,
        "url": url or f"https://{domain}",
        "status": status,
        "reason": formatted_reason,
    }
    if ip:
        set_fields["ip"] = ip
    if asn:
        set_fields["asn"] = asn
    if confidence is not None:
        set_fields["confidence"] = confidence
    if category is not None:
        set_fields["category"] = category
    if decided_by is not None:
        set_fields["decided_by"] = decided_by
    if ai_context is not None:
        set_fields["ai_context"] = ai_context
    if matched_keywords is not None:
        set_fields["matched_keywords"] = [str(k) for k in matched_keywords]
    if status != "unconfirmed":
        set_fields["unconfirmed_count"] = 0  # any real verdict clears the streak

    # screenshot_taken and screenshot_date are strictly for gambling status only. Only
    # touched when the caller passes an explicit True/False -- leaving screenshot_taken=None
    # (the default) means "don't know/don't care," not "no screenshot", so it must never
    # silently overwrite real, already-captured evidence to False. Every runner.py call site
    # already passes an explicit bool; this only protects a caller (e.g.
    # reported_blocked_checker.py reverting a domain back to "gambling") that doesn't.
    if status == "gambling" and screenshot_taken is not None:
        set_fields["screenshot_taken"] = bool(screenshot_taken)
        if screenshot_taken:
            set_fields["screenshot_date"] = today_date
            set_fields["screenshot_failed_reason"] = None
        elif screenshot_failed_reason is not None:
            # Bug fixed 2026-08-30: this parameter was accepted and displayed by the
            # dashboard/API but never actually written here -- every screenshot failure
            # reason runner.py passed in was silently dropped.
            set_fields["screenshot_failed_reason"] = screenshot_failed_reason

    update = {
        "$set": set_fields,
        "$setOnInsert": {"added_date": today_date, "source": "searxng_search"},
    }

    import time
    last_err = None
    for attempt in range(3):
        try:
            checked_domains().update_one(
                {"_id": domain},
                update,
                upsert=True,
            )

            # Sync status in source domain_Listed (strictly: _id, domain, active, processed, added_date, source)
            if status == "unconfirmed":
                # Track consecutive unconfirmed streaks. Past UNCONFIRMED_RETRY_CAP failures
                # in a row, stop letting --mode new re-pick this up as if it were a fresh
                # domain (it was just cluttering that queue with the same stuck domains
                # every run) -- but checked_domains.status stays "unconfirmed" regardless,
                # so --mode unconfirmed (the dedicated recheck queue, see
                # find_unconfirmed_domains -- it queries status directly, not this flag)
                # keeps finding and retrying it forever. Never hides a domain from review,
                # just stops double-counting it against the new-domains queue.
                updated = checked_domains().find_one_and_update(
                    {"_id": domain},
                    {"$inc": {"unconfirmed_count": 1}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                cap = int(os.getenv("UNCONFIRMED_RETRY_CAP", 5))
                give_up_on_new_queue = (updated or {}).get("unconfirmed_count", 0) >= cap
                source_domains().update_one(
                    {"_id": domain},
                    {
                        "$set": {"domain": domain, "processed": give_up_on_new_queue, "active": True},
                        "$setOnInsert": {"added_date": today_date, "source": "searxng_search"},
                    },
                    upsert=True,
                )
            elif status in ("blocked", "dead", "reported_down"):
                source_domains().update_one(
                    {"_id": domain},
                    {
                        "$set": {"domain": domain, "processed": True, "active": False},
                        "$setOnInsert": {"added_date": today_date, "source": "searxng_search"},
                    },
                    upsert=True,
                )
            else:  # gambling, regular, or for_sale
                source_domains().update_one(
                    {"_id": domain},
                    {
                        "$set": {"domain": domain, "processed": True, "active": True},
                        "$setOnInsert": {"added_date": today_date, "source": "searxng_search"},
                    },
                    upsert=True,
                )
            return
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                print(f"[write_result ERROR] Failed writing to MongoDB for domain '{domain}': {e}")
                raise last_err


def get_checked(domain: str) -> dict | None:
    return checked_domains().find_one({"_id": domain})


def find_pending_capture(limit: int = 0):
    """Gambling domains not yet screenshotted."""
    cur = checked_domains().find({"status": "gambling", "screenshot_taken": False})
    if limit:
        cur = cur.limit(limit)
    return cur


def find_unreviewed_gambling_domains(limit: int = 0):
    """Gambling verdicts that were locked by the heuristic classifier alone and never
    passed through the AI analyst/validator (reason == "<N> keywords matched", written by
    runner.py's decision == "gambling" branch). These are the records at risk from the
    classifier.py domain-anchor / actionable-signal false-positive bug — use
    `python -m checking_url.runner --mode gambling` to re-run them through the fixed
    heuristic + AI pipeline and correct any that were wrongly locked.
    """
    query = {
        "status": "gambling",
        "reason": {"$regex": r"^\d+ keywords matched$"},
    }
    cur = checked_domains().find(query)
    if limit:
        cur = cur.limit(limit)
    for doc in cur:
        domain = doc.get("domain") or doc.get("_id")
        if domain:
            yield {"domain": domain, "_id": domain, "url": doc.get("url", f"https://{domain}")}


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


def seed_file_to_domain_listed(file_path: str) -> int:
    """Extract domains from any .xlsx, .csv, or .txt file and seed them into domain_Listed."""
    domains = extract_domains_from_file(file_path)
    if not domains:
        print(f"[seed] No valid domains found in '{file_path}'")
        return 0
    today_date = datetime.now(IST).strftime("%Y-%m-%d")
    tag = f"imported from {Path(file_path).name} on {today_date}"
    inserted = 0
    for domain in domains:
        res = source_domains().update_one(
            {"_id": domain},
            {
                "$set": {"domain": domain, "active": True, "processed": False, "source": tag},
                "$setOnInsert": {"added_date": today_date},
            },
            upsert=True,
        )
        if res.upserted_id:
            inserted += 1
    print(f"[seed] Loaded {len(domains):,} unique domains from '{Path(file_path).name}' ({inserted:,} new inserted into domain_Listed)")
    return len(domains)


def seed_from_csv(path: str, active: bool = True):
    """Import domains from CSV into domain_Listed without overwriting existing domain statuses."""
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
                    "$set": {"_id": domain, "domain": domain},
                    "$setOnInsert": {"active": active, "processed": False, "added_date": today_date},
                },
                upsert=True,
            )
            inserted += 1
    print(f"[seed] {inserted} upserted into domain_Listed, {skipped} skipped")


def ingest_true_positives(path_or_domains: str | list[str]) -> tuple[int, int]:
    """Import confirmed gambling domains from an Excel (.xlsx/.xls), CSV, .txt file, or list of domain strings
    directly into checked_domains as status='gambling', screenshot_taken=False.

    Resets existing domains to status='gambling' for re-capture and re-export.
    Returns (inserted, reset).
    """
    inserted = reset = 0
    today_date = datetime.now(IST).strftime("%Y-%m-%d")

    if isinstance(path_or_domains, (list, tuple, set)):
        domains = []
        for raw in path_or_domains:
            if not raw:
                continue
            d = str(raw).strip().replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/").lower()
            d = extract_domain(f"https://{d}") or d
            if d and "." in d and d not in domains:
                domains.append(d)
    else:
        domains = extract_domains_from_file(path_or_domains)

    for domain in domains:
        if not domain or "." not in domain:
            continue

        # If already exists, reset for re-capture, re-export, and ensure status='gambling'
        existing = checked_domains().find_one({"_id": domain}, {"_id": 1})
        if existing:
            checked_domains().update_one(
                {"_id": domain},
                {
                    "$set": {
                        "status": "gambling",
                        "reason": "Manual true positive import",
                        "screenshot_taken": False,
                        "exported": False,
                        "screenshot_failed_reason": None,
                    }
                },
            )
            # Also ensure domain exists and is marked processed in source collection
            source_domains().update_one(
                {"_id": domain},
                {
                    "$set": {"domain": domain, "active": True, "processed": True},
                    "$setOnInsert": {"added_date": today_date, "source": "manual_import"},
                },
                upsert=True,
            )
            reset += 1
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
        # Also ensure domain exists and is marked processed in source collection
        source_domains().update_one(
            {"_id": domain},
            {
                "$set": {"domain": domain, "active": True, "processed": True},
                "$setOnInsert": {"added_date": today_date, "source": "manual_import"},
            },
            upsert=True,
        )
        inserted += 1

    return inserted, reset


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
        domain = domain.strip().lower().removeprefix("www.").rstrip("/")
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


def backup_databases(backup_dir: str = None) -> dict:
    """Dump source (domain_Listed) and destination (checked_domains) MongoDB collections to JSON backups."""
    import json
    get_db()

    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    if not backup_dir:
        backup_dir = os.path.join("output", "backups", f"backup_{ts}")
    os.makedirs(backup_dir, exist_ok=True)

    # 1. Backup domain_Listed
    source_file = os.path.join(backup_dir, "domain_Listed.json")
    source_docs = list(source_domains().find({}))
    for d in source_docs:
        d["_id"] = str(d["_id"])
    with open(source_file, "w", encoding="utf-8") as f:
        json.dump(source_docs, f, indent=2, default=str)

    # 2. Backup checked_domains
    checked_file = os.path.join(backup_dir, "checked_domains.json")
    checked_docs = list(checked_domains().find({}))
    for d in checked_docs:
        d["_id"] = str(d["_id"])
    with open(checked_file, "w", encoding="utf-8") as f:
        json.dump(checked_docs, f, indent=2, default=str)

    return {
        "status": "success",
        "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "backup_dir": os.path.abspath(backup_dir),
        "source_count": len(source_docs),
        "checked_count": len(checked_docs),
        "source_file": os.path.abspath(source_file),
        "checked_file": os.path.abspath(checked_file),
    }



