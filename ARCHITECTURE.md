# gamblingwebfind — Architecture

## Overview

**gamblingwebfind** is a four-stage Python pipeline for discovering, classifying, and documenting online gambling websites. All stages share a single MongoDB instance (`gamblingsites`) and a single `.env` configuration file at the project root.

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
│  Output : checked_domains { status, reason, checked_at, ... }        │
│           domain_Listed.processed = true   (stamps after each check) │
│  Export : output/verify_results.xlsx (4 sheets)                      │
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
│  STAGE 3 — capture_url/runner.py                                     │
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
| Just checked by Stage 2 | `true` | Done — Stage 2 will never touch it again |
| Imported from Excel via `import_output_excel.py` | `true` | Already has results — skip |

**How Stage 2 filters:** `{ active: true, processed: { $ne: true } }`  
Using `$ne: true` (not equal to true) means legacy documents without the field are treated as unprocessed — fully backward compatible.

**Atomicity:** `write_result()` in `db/mongo_client.py` writes to `checked_domains` AND flips `processed: true` on the source document in the same function call — no crash window between the two writes.

---

## MongoDB — Database: `gamblingsites`

### Collection 1: `domain_Listed` — Source of domains to process

```json
{
  "_id": "we-play.poker",
  "domain": "we-play.poker",
  "active": true,
  "processed": false,
  "added_date": "2026-08-13"
}
```

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `_id` | string | Stage 0/1 | Domain name (primary key) |
| `domain` | string | Stage 0/1 | e.g. `"bet365.com"` |
| `active` | bool/string | Stage 0/1 | `true` = reachable, `false` = dead, `"blocked"` = WAF |
| `processed` | bool | Stage 2 | `false` on insert; `true` after Stage 2 checks it |
| `added_date` | string | Stage 0/1 | First import date `YYYY-MM-DD` — set once, never overwritten |

### Collection 2: `checked_domains` — Classification results

```json
{
  "_id": "we-play.poker",
  "domain": "we-play.poker",
  "url": "https://we-play.poker",
  "status": "gambling",
  "reason": ["play now", "cashier", "casino", "poker", "wager", "responsible gambling"],
  "checked_at": "2026-08-13 19:45:00 IST",
  "screenshot_taken": true,
  "screenshot_failed_reason": null,
  "added_date": "2026-08-13",
  "exported": true,
  "exported_at": "2026-08-13"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `_id` | string | Domain name (primary key) |
| `domain` | string | Same as `_id` |
| `url` | string | Full normalized URL |
| `status` | string | `"gambling"` / `"regular"` / `"blocked"` / `"dead"` |
| `reason` | array | Matched keyword signals from the 500-keyword list |
| `checked_at` | string | Timestamp of classification in IST |
| `screenshot_taken` | bool | `true` after successful screenshot |
| `screenshot_failed_reason` | string | Error message if capture failed, else `null` |
| `added_date` | string | First import date — `YYYY-MM-DD`, set once |
| `exported` | bool | `true` once included in a report |
| `exported_at` | string | Export date — `YYYY-MM-DD` |

---

## File Structure & What Each File Does

```
gamblingwebfind/
│
├── .env                              ← Single config for all stages
├── .env.example                      ← Template with all variables documented
├── main.py                           ← Interactive menu entry point (Options 0–5)
├── gambling_top_500_keywords.json    ← 500 gambling signal keywords for Stage 2
├── requirements.txt                  ← All Python dependencies
│
├── keywordssearch/                   ← STAGE 0 — SearXNG web search
│   ├── searxng_search.py             ← Core: search → extract domains → save to MongoDB
│   │                                    • start_searxng_docker(): auto-starts Docker
│   │                                    • search_keyword_urls(): calls SearXNG JSON API
│   │                                    • save_domains_to_mongo(): $setOnInsert with processed:false
│   ├── docker-compose.yml            ← Defines Redis + SearXNG containers
│   └── searxng/
│       └── settings.yml              ← SearXNG config: JSON API on, web engines only
│
├── keywordsindomainfetch/            ← STAGE 1 — Common Crawl domain extraction
│   ├── find_domains.py               ← Main script:
│   │                                    • Downloads CC-MAIN-2024-22 parquet index
│   │                                    • DuckDB SQL LIKE queries for keyword domains
│   │                                    • DNS check → HTTP probe → MongoDB upsert
│   │                                    • Checkpoint/resume system
│   └── manifests/                    ← Cached CC crawl manifest (.warc.paths.gz)
│
├── db/                               ← Shared MongoDB layer (used by all stages)
│   └── mongo_client.py               ← Central DB module:
│                                        • get_db(): singleton MongoDB connection
│                                        • source_domains(): domain_Listed collection
│                                        • checked_domains(): checked_domains collection
│                                        • find_active_domains(): { active:true, processed:{$ne:true} }
│                                        • write_result(): writes to checked_domains AND flips processed:true atomically
│                                        • mark_domain_processed(): flip processed:true on source doc
│                                        • find_pending_capture(): gambling + screenshot_taken:false
│                                        • seed_from_csv(): bulk import from CSV file
│                                        • normalize_url(): strips tracking params, normalises scheme
│                                        • extract_domain(): tldextract registered domain
│                                        NOTE: _id (domain name) is the ONLY index — no extra indexes created
│
├── checking_url/                     ← STAGE 2 — URL fetching & classification
│   ├── runner.py                     ← Orchestrator:
│   │                                    • Loads keywords once at startup
│   │                                    • asyncio.gather() across all pending domains
│   │                                    • Semaphore-limited concurrency
│   ├── fetcher.py                    ← Two-tier HTTP fetcher:
│   │                                    Tier 1: Scrapling AsyncFetcher (curl_cffi, fast)
│   │                                    Tier 2: StealthyFetcher (real browser, Cloudflare solver)
│   │                                    • Per-domain rate limiting via checked_domains.last_updated_at
│   │                                    • Failure classification: blocked / dead_confirmed / connection_failed
│   └── classifier.py                 ← Gambling keyword matcher:
│                                        • load_keywords(): reads gambling_top_500_keywords.json
│                                        • _extract_text(): BeautifulSoup visible text + meta tags
│                                        • classify(): >= 3 matches → gambling; hard-excludes .gov/.edu
│
├── capture_url/                      ← STAGE 3 — Screenshot capture & reporting
│   ├── runner.py                     ← Orchestrator:
│   │                                    • Fetches gambling domains with screenshot_taken=false
│   │                                    • BrowserPool for concurrent Playwright captures
│   │                                    • Reclassifies dead/blocked on capture failure
│   ├── screenshot.py                 ← Playwright BrowserPool:
│   │                                    • Progressive nav strategy per retry (domcontentloaded → load → commit)
│   │                                    • wait_for_load_state('networkidle'): waits for all XHR to finish
│   │                                    • Scroll trigger: scrolls bottom → top to load lazy content
│   │                                    • Settle delay: 1s → 1.5s → 2.0s per retry
│   │                                    • is_valid_screenshot(): size + colour-variance validation
│   ├── excel_exporter.py             ← MongoDB → Excel workbooks:
│   │                                    • export_verify_workbook(): 4 sheets (Gambling/Blocked/Dead/Regular)
│   │                                    • export_capture_workbook(): 2 sheets (Captured/Failed)
│   └── docx_report_generator.py      ← MongoDB → Word report:
│                                        • 2 targets per page (hyperlink + screenshot)
│                                        • Auto-deletes temp JPEGs after embedding
│
└── project_sup/                      ← Manual utility scripts (run independently)
    └── mongodbupdate/
        ├── import_output_excel.py    ← Bulk Excel importer:
        │                                • Reads Verified/Rejected/Dead/Blocked sheets
        │                                • Upserts into domain_Listed (processed:true) + checked_domains
        │                                • $setOnInsert for added_date — never overwrites existing
        ├── update_export_status.py   ← Marks domains as exported in MongoDB:
        │                                • Sets exported:true, export_status, exported_at, screenshot_taken
        │                                • Reads from a CSV of already-exported domains
        └── arrange_docx_report.py    ← Post-processing Word report tool:
                                         • Re-arranges existing report to 2 items per page
                                         • Matches CSV target numbers to DOCX screenshot images
                                         • Supports batch splitting (e.g. 1000 targets per file)
```

---

## Output Directory Structure

All generated reports land in a single `output/` folder at the project root:

```
gamblingwebfind/
└── output/
    ├── verify_results.xlsx                        ← Stage 2 output (4 sheets)
    │                                                 Gambling | Blocked | Dead | Regular
    ├── capture_report_<YYYY-MM-DD_HH-MM-SS>.docx ← Stage 3 Word report
    │                                                 2 targets per page, clickable links
    └── capture_results_<YYYY-MM-DD_HH-MM-SS>.xlsx ← Stage 3 Excel (Captured / Failed)
```

---

## Configuration — `.env`

```env
# ── SearXNG Keyword Search (Stage 0) ──────────────────────────────────────────
# SearXNG runs via Docker (auto-started when you pick Option 0)
SEARXNG_BASE_URL=http://127.0.0.1:8080
SEARXNG_MAX_PAGES=4         # Pages per keyword (10 results/page)
SEARXNG_PAGE_DELAY=1.0      # Seconds between pages
SEARXNG_TIMEOUT=10.0        # HTTP timeout per request

# ── MongoDB Configuration ──────────────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_COLLECTION=domain_Listed        # Stage 0/1 output — Stage 2 source
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
