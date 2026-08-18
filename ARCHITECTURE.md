# gamblingwebfind — Architecture

## Overview

**gamblingwebfind** is a multi-stage Python pipeline for discovering, classifying, and documenting online gambling websites. All stages share a single MongoDB instance (`gamblingsites` by default, customizable via `.env`) and a single `.env` configuration file at the project root.

---

## Complete Pipeline

```
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 0 — keywordssearch/searxng_search.py                          │
│                                                                      │
│  Source : SearXNG (Docker) — searches Google, Bing, DuckDuckGo      │
│  Input  : Keywords typed by user (e.g. casino, poker, bet)          │
│  Method : HTTP JSON API calls to SearXNG running on localhost:8080   │
│           Extracts & normalises domains from all result URLs         │
│  Output : domain_Listed { domain, active:true, processed:false,      │
│                           added_date, source:"searxng_search" }      │
│  Docker : auto-starts docker-compose up -d if not already running   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  new domains with processed:false
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 1 — keywordsindomainfetch/find_domains.py                     │
│                                                                      │
│  Source : Common Crawl Parquet index (CC-MAIN-2024-22)              │
│  Method : DuckDB SQL LIKE queries over 300+ parquet files            │
│           DNS resolution + HTTP probe per extracted domain           │
│  Output : domain_Listed { domain, active, processed:false,           │
│                           added_date }                               │
│  Scale  : Processes millions of crawl records per batch              │
│  Resume : Checkpoint system — picks up after crash/interrupt         │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  active:true, processed:false
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 2 — checking_url/runner.py                                    │
│                                                                      │
│  Input  : domain_Listed WHERE active=true AND processed != true      │
│  Method : Scrapling AsyncFetcher (curl_cffi TLS impersonation)       │
│           Optional escalation to StealthyFetcher (real browser,      │
│           solve_cloudflare=True) if STEALTH_FALLBACK=true            │
│           → BeautifulSoup text extraction                            │
│           → Keyword matching against gambling_top_500_keywords.json  │
│           Rule: >= 3 keyword matches → status = "gambling"           │
│  Deep   : checking_url/deep_crawl.py                                 │
│           Extracts outbound gambling links from aggregator pages     │
│           Saves new domains → domain_Listed { source:"deep_crawl" }  │
│  Output : checked_domains { status, reason, added_date, ... }        │
│           domain_Listed.processed = true   (stamps after each check) │
│  Export : output/verify_results.xlsx (4 sheets, clickable links)     │
│                                                                      │
│  Status values written:                                              │
│    gambling  — >= 3 keyword matches confirmed                        │
│    regular   — reachable but not gambling (< 3 matches)              │
│    blocked   — 403/429 or Cloudflare WAF challenge                   │
│    dead      — DNS fail / connection refused / parked page           │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  status="gambling", screenshot_taken=false
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 3 — export_domains/exporter.py                                │
│                                                                      │
│  Input  : checked_domains WHERE status=gambling,                     │
│                                screenshot_taken=false                │
│  Method : Playwright headless Chromium BrowserPool                   │
│           3-layer load completion:                                   │
│             1. wait_for_load_state('networkidle')  ← all XHR done   │
│             2. Scroll to bottom → back to top      ← lazy content   │
│             3. Progressive settle delay per retry  ← slow sites     │
│           JPEG q68, 1280×900, 3 retries per domain                  │
│  Output : output/capture_report_<date>.docx   (2 targets per page)  │
│           output/capture_results_<date>.xlsx  (Captured/Failed)     │
│           Temp JPEGs in output/screenshots/ auto-deleted after embed │
└──────────────────────────────────────────────────────────────────────┘
```

---

## The `processed` Flag — Anti-Duplicate System

Every document in `domain_Listed` carries a `processed` field that prevents Stage 2 from ever re-checking a domain that has already been processed.

| State | Value | Meaning |
|-------|-------|---------|
| Newly inserted by Stage 0 or Stage 1 | `false` | Not yet checked — eligible for Stage 2 |
| Discovered via Deep Crawl in Stage 2 | `false` | Outbound domain from aggregator — eligible for Stage 2 |
| Just checked by Stage 2 | `true` | Done — Stage 2 will never touch it again |
| Synced via `compare_processed_domains.py` | `true` | Matched with existing results in `checked_domains` |

**How Stage 2 filters:** `{ active: true, processed: { $ne: true } }`  
Using `$ne: true` (not equal to true) means legacy documents without the field are treated as unprocessed — fully backward compatible.

**Atomicity:** `write_result()` in `db/mongo_client.py` writes to `checked_domains` AND flips `processed: true` on the source document in the same function call.

---

## MongoDB Schema Specifications

### Collection 1: `domain_Listed` — Source of domains to process

```json
{
  "_id": "we-play.poker",
  "domain": "we-play.poker",
  "active": true,
  "processed": false,
  "added_date": "2026-08-13",
  "source": "searxng_search"
}
```

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `_id` | string | Stage 0/1/DeepCrawl | Domain name (primary key) |
| `domain` | string | Stage 0/1/DeepCrawl | e.g. `"bet365.com"` |
| `active` | bool/string | Stage 0/1 | `true` = reachable, `false` = dead, `"blocked"` = WAF |
| `processed` | bool | Stage 2 | `false` on insert; `true` after Stage 2 checks it |
| `added_date` | string | Stage 0/1/DeepCrawl | First import date `YYYY-MM-DD` — set once via `$setOnInsert` |
| `source` | string | Stage 0/1/DeepCrawl | Origin e.g. `"searxng_search"`, `"common_crawl"`, `"deep_crawl"` |
| `discovered_from` | string | Stage 2 DeepCrawl | Aggregator domain that contained the link (optional) |

### Collection 2: `checked_domains` — Minimal classification results

Documents follow a strict minimal schema to eliminate database bloat:

```json
// Gambling domain (before capture/export)
{
  "_id": "we-play.poker",
  "domain": "we-play.poker",
  "url": "https://we-play.poker",
  "status": "gambling",
  "reason": ["play now", "cashier", "casino", "poker", "wager"],
  "added_date": "2026-08-13",
  "screenshot_taken": false,
  "screenshot_failed_reason": null
}

// Regular / Blocked / Dead domain
{
  "_id": "example.org",
  "domain": "example.org",
  "url": "https://example.org",
  "status": "regular",
  "reason": ["sports"],
  "added_date": "2026-08-13"
}
```

| Field | Type | Present On | Description |
|-------|------|------------|-------------|
| `_id` | string | All docs | Domain name (primary key) |
| `domain` | string | All docs | Same as `_id` |
| `url` | string | All docs | Full normalized URL |
| `status` | string | All docs | `"gambling"` / `"regular"` / `"blocked"` / `"dead"` |
| `reason` | array | All docs | Matched keyword signals |
| `added_date` | string | All docs | First import date — `YYYY-MM-DD` |
| `screenshot_taken` | bool | `gambling` only | `true` after successful screenshot |
| `screenshot_failed_reason` | string | `gambling` only | Error message if capture failed, else `null` |
| `exported` | bool | Exported docs only | `true` once included in an export report |
| `exported_at` | string | Exported docs only | Export date — `YYYY-MM-DD` |

---

## File Structure & Module Organization

```
gamblingwebfind/
│
├── .env                              ← Single config for all pipeline stages
├── .env.example                      ← Template with all variables documented
├── main.py                           ← Interactive menu entry point (Options 0–4)
├── gambling_top_500_keywords.json    ← 500 gambling signal keywords for Stage 2
├── requirements.txt                  ← All Python dependencies
│
├── keywordssearch/                   ← STAGE 0 — SearXNG web search
│   ├── searxng_search.py             ← Docker auto-start + search + save to MongoDB
│   ├── docker-compose.yml            ← Redis + SearXNG container definitions
│   └── searxng/
│       └── settings.yml              ← SearXNG config (JSON API enabled)
│
├── keywordsindomainfetch/            ← STAGE 1 — Common Crawl domain extraction
│   ├── find_domains.py               ← Common Crawl DuckDB SQL query → DNS/HTTP probe
│   └── manifests/                    ← Cached CC crawl manifest files
│
├── db/                               ← Shared MongoDB layer
│   └── mongo_client.py               ← Connection singleton, write_result(), seed_discovered_domains()
│
├── checking_url/                     ← STAGE 2 — URL fetching & classification
│   ├── runner.py                     ← Orchestrator (async fetch + classification + deep crawl hook)
│   ├── fetcher.py                    ← Two-tier HTTP fetcher (Fast AsyncFetcher + StealthyFetcher)
│   ├── classifier.py                 ← Keyword matcher & parked lander detector
│   └── deep_crawl.py                 ← Outbound link extractor for aggregator gambling pages
│
├── export_domains/                   ← STAGE 3 — Word, PDF & Excel Report Export & Batch Splitting
│   ├── exporter.py                   ← Unified single-command export pipeline
│   ├── screenshot.py                 ← Screenshot validation & image utilities
│   └── batch_splitter.py             ← Splits Excel/CSV and PDF reports into batch subfolders
│
└── project_sup/                      ← Project support & utility scripts
    └── helping_code/
        ├── audit_db.py               ← Database health check & interactive/automated schema cleanup
        ├── cleanup_db.py             ← Wrapper delegating directly to audit_db.py --fix
        ├── compare_dbs.py            ← Interactive tool to compare & sync domains between two DBs
        ├── compare_processed_domains.py.py ← Syncs processed=True flag for domains in checked_domains
        ├── import_output_excel.py    ← Bulk Excel importer into MongoDB
        ├── merge_pdfs.py             ← PDF merger tool
        └── arrange_docx_report.py    ← Post-processing Word report formatting tool
```

---

## Output Directory Structure

All generated reports land in a timestamped folder inside `output/` with flexible export modes:

```
gamblingwebfind/
└── output/
    └── <YYYYMMDD_HHMMSS>/
        ├── captured_domains_combined.xlsx         ← Single Master Excel (if Single/Both chosen)
        ├── capture_report_combined.docx           ← Single Master Word (if Single/Both chosen)
        ├── capture_report_combined.pdf            ← Single Master PDF (if Single/Both chosen)
        │
        ├── batch_001/                             ← Batched Deliverables (if Batch/Both chosen)
        │   ├── batch_001_domains.xlsx
        │   ├── batch_001_report.docx
        │   └── batch_001_report.pdf
        ├── batch_002/
        │   ├── batch_002_domains.xlsx
        │   ├── batch_002_report.docx
        │   └── batch_002_report.pdf
        │
        └── failed_domains.xlsx                    ← Failed domains with failure reasons
```


---

## Configuration — `.env`

```env
# ── SearXNG Keyword Search (Stage 0) ──────────────────────────────────────────
SEARXNG_BASE_URL=http://127.0.0.1:8080
SEARXNG_MAX_PAGES=4
SEARXNG_PAGE_DELAY=1.0
SEARXNG_TIMEOUT=10.0

# ── MongoDB Configuration ──────────────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_DB2_NAME=gamblingsitetry
MONGO_COLLECTION=domain_Listed
CHECKED_COLLECTION=checked_domains

# ── Stage 1 — Common Crawl Tuning ─────────────────────────────────────────────
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
PARQUET_BATCH_SIZE=20
EXPORT_TO_MONGO=true

# ── Stage 2 — URL Checking Tuning ─────────────────────────────────────────────
FETCH_TIMEOUT=10
PER_DOMAIN_DELAY=2.0
MAX_CONCURRENT_FETCHES=20
STEALTH_FALLBACK=false
STEALTH_TIMEOUT=60
STEALTH_CONCURRENCY=3

# ── Deep Crawl Link Extraction ────────────────────────────────────────────────
DEEP_CRAWL_MIN_LINKS=3
DEEP_CRAWL_STRICT=false

# ── Stage 3 — Screenshot Tuning ───────────────────────────────────────────────
SCREENSHOT_CONCURRENCY=15
```

---

## Key Architectural Principles

1. **`processed` flag:** Every domain is checked at most once. Stage 2 only picks up `processed != true` documents. Once checked, `write_result()` atomically flips it to `true` alongside writing the result.
2. **Deep Crawl Aggregator Discovery:** When an aggregator page is identified as gambling, outbound external links are extracted, filtered against a strict blocklist, and seeded into `domain_Listed` for future checks.
3. **Clickable Hyperlinks:** All exported URLs in Excel, Word, and PDF reports are built with active `https://` OpenXML and openpyxl web hyperlinks.
4. **Minimal Schema Design:** No bloat fields (`checked_at`, `last_updated_at`). Screenshot and export metadata exist only on relevant documents.
5. **Unified `.env` Config:** Single `.env` drives pipeline execution and helper utility tools.
6. **Automated DB Health & Cleanup:** `audit_db.py` audits schema health and repairs bloat or missing flags safely.
ce
CHECKED_COLLECTION=checked_domains    # Stage 2/3 output
KEYWORDS_COLLECTION=keywords

# ── Stage 1 — Common Crawl Tuning ─────────────────────────────────────────────
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
PARQUET_BATCH_SIZE=20
EXPORT_TO_MONGO=true

# ── Stage 2 — URL Checking Tuning ─────────────────────────────────────────────
FETCH_TIMEOUT=10
PER_DOMAIN_DELAY=2.0
MAX_CONCURRENT_FETCHES=20
STEALTH_FALLBACK=false      # true = retry blocked domains with a real browser
STEALTH_TIMEOUT=60
STEALTH_CONCURRENCY=3

# ── Stage 3 — Screenshot Tuning ───────────────────────────────────────────────
SCREENSHOT_CONCURRENCY=15
```

---

## How to Run

```bash
python main.py
```

Interactive menu:

```
=======================================================
           GAMBLINGWEBFIND PROCESS MENU
=======================================================
0. SearXNG Keyword Search (Stage 0) — Docker
1. Keywords Search in Domain Fetch (Stage 1) — Common Crawl
2. Checking URL (Stage 2)
3. Capture URL (Stage 3)
4. Both Checking & Capture URL (Stage 2 + Stage 3)
5. Exit
=======================================================
```

---

## Key Architectural Principles

1. **`processed` flag:** Every domain is checked at most once. Stage 2 only picks up `processed != true` documents. Once checked, `write_result()` atomically flips it to `true` alongside writing the result — no crash window.
2. **`added_date` tracking:** Set once via `$setOnInsert` — never overwritten on re-import.
3. **Dual domain sources:** Stage 0 finds domains from live web search; Stage 1 finds domains from historical crawl data. Both feed the same `domain_Listed` collection.
4. **Three-layer screenshot completion:** networkidle wait → scroll trigger → settle delay ensures pages are fully rendered before capture.
5. **Single `.env` config:** One file drives all four pipeline stages.
6. **Single `output/` folder:** All reports land in one clean location.
7. **Auto-cleanup:** Screenshot JPEGs are embedded into Word and deleted from disk immediately.
8. **`$ne: true` backward compatibility:** Domains inserted before the `processed` field existed are treated as unprocessed and will still be checked.
