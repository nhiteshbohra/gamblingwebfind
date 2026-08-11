# Common Crawl Domain Finder & Verification Pipeline

A unified, high-performance Python script to extract registered domains from Common Crawl datasets, verify domain status (`active`: `true` / `false` / `"blocked"`), and store results directly into **MongoDB**.

---

## Workflow Diagram

```
                 Common Crawl
                      │
                      ▼
              Extract Domain (DuckDB)
                      │
                      ▼
             Normalize Domain
                      │
                      ▼
              Deduplicate
                      │
                      ▼
                 DNS Check
                /         \
             FAIL         PASS
              │            │
              ▼            ▼
         active: false  HTTP/HTTPS Probe
                         /          \
                    REACHABLE     BLOCKED
                       │             │
                       ▼             ▼
                  active: true  active: "blocked"
                       │             │
                       └──────┬──────┘
                              │
                              ▼
                       Store in MongoDB
```

---

## Features

1. **DuckDB Extraction**: Fast SQL querying directly over Common Crawl WARC Parquet index files.
2. **Domain Normalization & Deduplication**: Cleans, lowercases, and deduplicates extracted domain hostnames.
3. **Async Status Verification**:
   - **`active: true`**: Website is live and reachable over HTTP/HTTPS.
   - **`active: false`**: Website DNS resolution failed or connection dropped.
   - **`active: "blocked"`**: Website is blocked by Cloudflare or WAF anti-bot security protection.
4. **Direct MongoDB Storage**: Stores domain documents into MongoDB using bulk upserts (`bulk_write`) with unique indexing on `domain`.
5. **Environment Configuration**: Configured via `.env` for database connection URIs, collection names, concurrency/worker threads, timeouts, and batch sizes.

---

## MongoDB Document Schemas

### 1. Active / Reachable Website:
```json
{
  "_id": ObjectId("66b93a1f8b42e719c8d1e23f"),
  "domain": "bet365.com",
  "active": true
}
```

### 2. Inactive / Offline / Unreachable Website:
```json
{
  "_id": ObjectId("66b93a1f8b42e719c8d1e240"),
  "domain": "example.com",
  "active": false
}
```

### 3. Blocked by Cloudflare / Anti-Bot Security:
```json
{
  "_id": ObjectId("66b93a1f8b42e719c8d1e241"),
  "domain": "protected-domain.com",
  "active": "blocked"
}
```

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Copy `.env.example` to `.env`:
```env
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=domain_finder
MONGO_COLLECTION=domains
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
EXPORT_TO_MONGO=true
```

### 3. Run Pipeline
Search for domains containing keyword(s), evaluate `active` status (`true` / `false` / `"blocked"`), and store directly in MongoDB:
```bash
python find_domains.py --keyword bet casino
python find_domains.py --keywords bet casino slot spin win play 777 lucky vegas jili
```

Custom worker threads and timeout:
```bash
python find_domains.py --keyword poker --workers 300 --timeout 3.0
```
