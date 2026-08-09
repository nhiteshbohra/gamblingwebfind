CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('seed', 'extracted')),
    source_url TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    times_used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    discovered_via_keyword_id INTEGER,
    status TEXT NOT NULL CHECK(status IN ('pending', 'verified', 'rejected', 'dead', 'blocked', 'disputed', 'removed')),
    confidence_score REAL DEFAULT 0.0,
    classification_reasons TEXT,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMP,
    verified_at TIMESTAMP,
    verification_tier TEXT NULL,  -- 'aggressive' (normal crawler) or 'strict' (batch command)
    FOREIGN KEY(discovered_via_keyword_id) REFERENCES keywords(id)
);

CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    last_fetched_at TIMESTAMP,
    fetch_count INTEGER DEFAULT 0,
    confirmed_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status);
CREATE INDEX IF NOT EXISTS idx_urls_domain ON urls(domain);
CREATE INDEX IF NOT EXISTS idx_keywords_term ON keywords(term);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Migrations: run after CREATE_TABLES_SQL so existing DBs get new columns/constraints.
# Run via db.py's init_db() which catches errors on already-applied migrations.
MIGRATE_SQL = [
    # Step 1: add verification_tier column (idempotent — caught if duplicate)
    "ALTER TABLE urls ADD COLUMN verification_tier TEXT NULL",

    # Steps 2–5: widen CHECK to include 'blocked' via table rebuild (SQLite pattern).
    # These are run as a script block in db.py using executescript().
    # Guarded by existence of urls_new so re-running is safe.
    """
    CREATE TABLE IF NOT EXISTS urls_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        domain TEXT NOT NULL,
        discovered_via_keyword_id INTEGER,
        status TEXT NOT NULL CHECK(status IN ('pending','verified','rejected','dead','blocked','disputed','removed')),
        confidence_score REAL DEFAULT 0.0,
        classification_reasons TEXT,
        first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_checked_at TIMESTAMP,
        verified_at TIMESTAMP,
        verification_tier TEXT NULL,
        FOREIGN KEY(discovered_via_keyword_id) REFERENCES keywords(id)
    );
    INSERT OR IGNORE INTO urls_new SELECT id,url,domain,discovered_via_keyword_id,status,
        confidence_score,classification_reasons,first_seen_at,last_checked_at,verified_at,verification_tier
        FROM urls;
    DROP TABLE urls;
    ALTER TABLE urls_new RENAME TO urls;
    CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status);
    CREATE INDEX IF NOT EXISTS idx_urls_domain ON urls(domain);
    """,
]
