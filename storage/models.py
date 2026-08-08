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
    status TEXT NOT NULL CHECK(status IN ('pending', 'verified', 'rejected', 'dead', 'disputed', 'removed')),
    confidence_score REAL DEFAULT 0.0,
    classification_reasons TEXT,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMP,
    verified_at TIMESTAMP,
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
