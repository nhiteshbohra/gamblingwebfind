import ssl
import socket
import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def get_ssl_info(domain: str) -> Dict[str, str]:
    """Connects via SSL socket to extract certificate details (issuer, valid_from, valid_to)."""
    ssl_data = {"ssl_issuer": "", "ssl_valid_from": "", "ssl_valid_to": ""}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                if cert:
                    issuer_dict = dict(x[0] for x in cert.get("issuer", []))
                    ssl_data["ssl_issuer"] = issuer_dict.get("organizationName") or issuer_dict.get("commonName") or ""
                    ssl_data["ssl_valid_from"] = cert.get("notBefore", "")
                    ssl_data["ssl_valid_to"] = cert.get("notAfter", "")
    except Exception as e:
        logger.debug(f"SSL cert fetch skipped/failed for {domain}: {e}")

    return ssl_data


def get_whois_info(domain: str) -> Dict[str, str]:
    """Queries WHOIS registration details (registrar, created_date, expiry_date)."""
    whois_data = {"whois_registrar": "", "whois_created_date": "", "whois_expiry_date": ""}
    try:
        import whois
        w = whois.whois(domain)
        if w:
            whois_data["whois_registrar"] = str(w.registrar) if w.registrar else ""
            
            created = w.creation_date
            if isinstance(created, list):
                created = created[0]
            whois_data["whois_created_date"] = str(created) if created else ""

            expiry = w.expiration_date
            if isinstance(expiry, list):
                expiry = expiry[0]
            whois_data["whois_expiry_date"] = str(expiry) if expiry else ""
    except Exception as e:
        logger.debug(f"WHOIS lookup skipped/failed for {domain}: {e}")

    return whois_data


async def enrich_url_record(url_id: int, domain: str, db) -> bool:
    """Enriches a single domain record with SSL & WHOIS data and updates DB."""
    ssl_data = await asyncio.to_thread(get_ssl_info, domain)
    whois_data = await asyncio.to_thread(get_whois_info, domain)

    await db.update_enrichment(url_id, ssl_data, whois_data)
    return True


async def enrich_pending_verified(db, concurrency: int = 3, limit: int = 100) -> int:
    """Enriches verified URLs in DB that lack WHOIS/SSL metadata."""
    rows = await db.get_unenriched_verified_urls(limit=limit)
    if not rows:
        return 0

    sem = asyncio.Semaphore(concurrency)
    count = 0

    async def _worker(row):
        nonlocal count
        async with sem:
            await enrich_url_record(row['id'], row['domain'], db)
            count += 1

    await asyncio.gather(*[_worker(r) for r in rows])
    return count
