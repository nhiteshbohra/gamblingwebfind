import os
import json
import sqlite3
import aiosqlite
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
        """keyword_tuples: list of (term, source, source_url)"""
        async with self._write_lock:
            async with self._connect() as db:
                await db.executemany(
                    "INSERT OR IGNORE INTO keywords (term, source, source_url) VALUES (?, ?, ?)",
                    keyword_tuples
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

    async def mark_status(self, url_id, status, confidence_score=0.0, reasons=None):
        reasons_str = json.dumps(reasons) if reasons is not None else None
        async with self._write_lock:
            async with self._connect() as db:
                if status == 'verified':
                    await db.execute(
                        """UPDATE urls SET status = ?, confidence_score = ?, classification_reasons = ?, 
                           last_checked_at = CURRENT_TIMESTAMP, verified_at = CURRENT_TIMESTAMP WHERE id = ?""",
                        (status, confidence_score, reasons_str, url_id)
                    )
                else:
                    await db.execute(
                        """UPDATE urls SET status = ?, confidence_score = ?, classification_reasons = ?, 
                           last_checked_at = CURRENT_TIMESTAMP WHERE id = ?""",
                        (status, confidence_score, reasons_str, url_id)
                    )
                await db.commit()

    async def touch_domain(self, domain, confirmed=False):
        async with self._write_lock:
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

    async def is_strict_processed(self, url: str) -> bool:
        """Return True if this URL is fully settled by verify-batch (resumability gate).
        'blocked' is intentionally excluded — blocked domains stay eligible for re-run
        once a better check method (headless browser, different IP) is available.
        """
        async with self._connect() as db:
            async with db.execute(
                "SELECT 1 FROM urls WHERE url = ? AND verification_tier = 'strict'"
                " AND status IN ('dead', 'verified', 'rejected')",
                (url,)
            ) as cursor:
                return (await cursor.fetchone()) is not None

    async def upsert_strict_result(self, url: str, domain: str, status: str,
                                   confidence_score: float = 0.0, reasons=None):
        """Insert-or-update a URL with verification_tier='strict'.
        Called by verify-batch for all three outcomes.
        """
        reasons_str = json.dumps(reasons) if reasons is not None else None
        verified_at_expr = ", verified_at = CURRENT_TIMESTAMP" if status == 'verified' else ""
        async with self._write_lock:
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO urls (url, domain, status, confidence_score, classification_reasons,"
                    " last_checked_at, verification_tier) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'strict')"
                    " ON CONFLICT(url) DO UPDATE SET status = excluded.status,"
                    " confidence_score = excluded.confidence_score,"
                    " classification_reasons = excluded.classification_reasons,"
                    " last_checked_at = CURRENT_TIMESTAMP,"
                    " verification_tier = 'strict'" + verified_at_expr,
                    (url, domain, status, confidence_score, reasons_str)
                )
                await db.commit()
