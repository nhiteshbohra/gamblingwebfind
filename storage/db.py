import os
import json
import aiosqlite
import pandas as pd
from storage.models import CREATE_TABLES_SQL, MIGRATE_SQL

import asyncio

class Database:
    def __init__(self, db_path="data/presence.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._write_lock = asyncio.Lock()

    def _connect(self):
        return aiosqlite.connect(self.db_path, timeout=60.0)

    async def init_db(self, seed_keywords_file=None):
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=60000;")
            await db.executescript(CREATE_TABLES_SQL)
            await db.commit()
            # Single-line migrations (ALTER TABLE ADD COLUMN)
            for stmt in MIGRATE_SQL:
                if '\n' not in stmt.strip():
                    try:
                        await db.execute(stmt)
                        await db.commit()
                    except Exception:
                        pass  # duplicate column = already migrated

        # Multi-statement migrations (table rebuilds) need their own connection
        # because executescript() issues an implicit COMMIT that conflicts with
        # an open aiosqlite transaction context.
        for stmt in MIGRATE_SQL:
            if '\n' in stmt.strip():
                # Gate: check if migration is already applied before running
                async with self._connect() as db:
                    async with db.execute(
                        "SELECT sql FROM sqlite_master WHERE name='urls' AND type='table'"
                    ) as c:
                        row = await c.fetchone()
                        schema = row[0] if row else ''
                    if "'blocked'" in schema:
                        continue  # already applied
                    try:
                        await db.executescript(stmt)
                    except Exception:
                        pass  # locked or already applied, will retry next init

        if seed_keywords_file and os.path.exists(seed_keywords_file):
            with open(seed_keywords_file, 'r', encoding='utf-8') as f:
                seed_terms = [line.strip() for line in f if line.strip()]
            await self.insert_keywords([(term, 'seed', None) for term in seed_terms])

    async def insert_keywords(self, keyword_tuples):
        """keyword_tuples: list of (term, source, source_url) or (term, source, source_url, type)"""
        normalized = []
        for item in keyword_tuples:
            if len(item) == 3:
                normalized.append((item[0], item[1], item[2], 'keyword'))
            else:
                normalized.append((item[0], item[1], item[2], item[3]))
        async with self._write_lock:
            async with self._connect() as db:
                await db.executemany(
                    "INSERT OR IGNORE INTO keywords (term, source, source_url, type) VALUES (?, ?, ?, ?)",
                    normalized
                )
                await db.commit()

    async def get_next_keywords(self, limit=5):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM keywords ORDER BY last_used_at ASC NULLS FIRST, times_used ASC LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()

    async def touch_keyword(self, keyword_id):
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    "UPDATE keywords SET last_used_at = CURRENT_TIMESTAMP, times_used = times_used + 1 WHERE id = ?",
                    (keyword_id,)
                )
                await db.commit()

    async def is_url_known(self, url):
        async with self._connect() as db:
            async with db.execute("SELECT 1 FROM urls WHERE url = ?", (url,)) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def upsert_url(self, url, domain, keyword_id):
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO urls (url, domain, discovered_via_keyword_id, status) VALUES (?, ?, ?, 'pending')",
                    (url, domain, keyword_id)
                )
                await db.commit()

    async def get_pending_urls(self, limit=50):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM urls WHERE status = 'pending' LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()



    async def touch_domain(self, domain, confirmed=False):
        async with self._write_lock:
            try:
                async with self._connect() as db:
                    await db.execute(
                        """INSERT INTO domains (domain, last_fetched_at, fetch_count, confirmed_count) 
                           VALUES (?, CURRENT_TIMESTAMP, 1, ?)
                           ON CONFLICT(domain) DO UPDATE SET 
                           last_fetched_at = CURRENT_TIMESTAMP,
                           fetch_count = fetch_count + 1,
                           confirmed_count = confirmed_count + ?""",
                        (domain, 1 if confirmed else 0, 1 if confirmed else 0)
                    )
                    await db.commit()
            except Exception:
                pass  # Bookkeeping write; swallow transient lock errors

    async def get_url_id(self, url) -> int | None:
        async with self._connect() as db:
            async with db.execute("SELECT id FROM urls WHERE url = ?", (url,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def mark_status_liveness(self, url, alive: bool):
        """Touch last_checked_at on success; status change on failure is done via mark_status()."""
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    "UPDATE urls SET last_checked_at = CURRENT_TIMESTAMP WHERE url = ?",
                    (url,)
                )
                await db.commit()

    async def get_last_domain_fetch_time(self, domain):
        async with self._connect() as db:
            async with db.execute("SELECT last_fetched_at FROM domains WHERE domain = ?", (domain,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_verified_live_rows(self):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT url, domain, confidence_score, first_seen_at, verified_at FROM urls WHERE status = 'verified' ORDER BY verified_at DESC"
            ) as cursor:
                return await cursor.fetchall()

    async def get_stats(self):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT status, COUNT(*) FROM urls GROUP BY status") as cursor:
                url_counts = dict(await cursor.fetchall())
            async with db.execute("SELECT COUNT(*) FROM keywords") as cursor:
                keyword_count = (await cursor.fetchone())[0]
            async with db.execute("SELECT value FROM meta WHERE key='last_export_at'") as cursor:
                row = await cursor.fetchone()
                last_export_at = row[0] if row else None
            
            # Discovery in last 15m & 1h
            async with db.execute("SELECT COUNT(*) FROM urls WHERE first_seen_at >= datetime('now', '-15 minutes')") as cursor:
                discovered_15m = (await cursor.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM urls WHERE verified_at >= datetime('now', '-15 minutes')") as cursor:
                verified_15m = (await cursor.fetchone())[0]
                
            # Latest 5 verified domains
            async with db.execute("SELECT url, domain, verified_at FROM urls WHERE status = 'verified' ORDER BY verified_at DESC LIMIT 5") as cursor:
                latest_verified = [dict(r) for r in await cursor.fetchall()]

            return {
                "urls": url_counts,
                "keywords": keyword_count,
                "last_export_at": last_export_at,
                "discovered_15m": discovered_15m,
                "verified_15m": verified_15m,
                "latest_verified": latest_verified
            }

    async def set_meta(self, key: str, value: str):
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value)
                )
                await db.commit()

    async def import_urls(self, rows: list) -> dict:
        """Insert a list of normalized (url, domain) tuples as pending, skipping duplicates.
        Uses the same is_duplicate() path as the discovery pipeline.
        """
        from core.dedup import is_duplicate
        new_count = 0
        skipped_count = 0
        for url, domain in rows:
            if await is_duplicate(url, db=self):
                skipped_count += 1
                continue
            await self.upsert_url(url=url, domain=domain, keyword_id=None)
            new_count += 1
        return {"new": new_count, "skipped": skipped_count}

    async def get_settled_urls_set(self, tier: str = None) -> set:
        """Return set of normalized URLs already settled (verified, rejected, or dead).
        If tier is provided, filters strictly by verification_tier.
        """
        query = "SELECT url FROM urls WHERE status IN ('dead', 'verified', 'rejected')"
        params = []
        if tier is not None:
            query += " AND verification_tier = ?"
            params.append(tier)
        async with self._connect() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return {r[0] for r in rows}

    async def get_unenriched_verified_urls(self, limit=100):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, url, domain FROM urls WHERE status = 'verified' AND (ssl_issuer IS NULL AND whois_registrar IS NULL) LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()

    async def record_classification(self, url: str = None, domain: str = None, status: str = 'pending',
                                     confidence_score: float = 0.0, reasons=None,
                                     tier: str = None, url_id: int = None):
        """Unified classification record method for all pipelines (crawler, batch verification, dispute).
        Uses INSERT ... ON CONFLICT(url) DO UPDATE with 3-attempt retry and exponential backoff.
        """
        reasons_str = json.dumps(reasons) if (reasons is not None and not isinstance(reasons, str)) else (reasons or None)
        verified_at_expr = ", verified_at = CURRENT_TIMESTAMP" if status == 'verified' else ""
        async with self._write_lock:
            for attempt in range(3):
                try:
                    async with self._connect() as db:
                        if not url and url_id:
                            await db.execute(
                                "UPDATE urls SET status = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (status, url_id)
                            )
                        else:
                            await db.execute(
                                "INSERT INTO urls (url, domain, status, confidence_score, classification_reasons,"
                                " last_checked_at, verification_tier) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)"
                                " ON CONFLICT(url) DO UPDATE SET status = excluded.status,"
                                " confidence_score = excluded.confidence_score,"
                                " classification_reasons = excluded.classification_reasons,"
                                " last_checked_at = CURRENT_TIMESTAMP,"
                                " verification_tier = COALESCE(excluded.verification_tier, urls.verification_tier)" + verified_at_expr,
                                (url, domain, status, confidence_score, reasons_str, tier)
                            )
                        await db.commit()
                        break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(0.5 * (attempt + 1))

    async def update_enrichment(self, url_id: int, ssl_data: dict, whois_data: dict):
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    """UPDATE urls SET 
                       ssl_issuer = ?, ssl_valid_from = ?, ssl_valid_to = ?,
                       whois_registrar = ?, whois_created_date = ?, whois_expiry_date = ?
                       WHERE id = ?""",
                    (
                        ssl_data.get('ssl_issuer', ''),
                        ssl_data.get('ssl_valid_from', ''),
                        ssl_data.get('ssl_valid_to', ''),
                        whois_data.get('whois_registrar', ''),
                        whois_data.get('whois_created_date', ''),
                        whois_data.get('whois_expiry_date', ''),
                        url_id
                    )
                )
                await db.commit()


def get_urls_export_df(db_path: str, status: str) -> pd.DataFrame:
    """Helper function for exporting pandas DataFrame per status category without inline SQL in exporters."""
    import sqlite3
    import pandas as pd
    conn = sqlite3.connect(db_path)
    if status == 'verified':
        query = "SELECT url, domain, confidence_score, classification_reasons as matched_signals, verified_at as checked_at FROM urls WHERE status = 'verified' ORDER BY verified_at DESC"
    elif status == 'rejected':
        query = "SELECT url, domain, confidence_score, classification_reasons as matched_signals, last_checked_at as checked_at FROM urls WHERE status = 'rejected' ORDER BY last_checked_at DESC"
    else:
        query = f"SELECT url, domain, last_checked_at as checked_at FROM urls WHERE status = '{status}' ORDER BY last_checked_at DESC"

    df = pd.read_sql_query(query, conn)
    conn.close()
    return df
