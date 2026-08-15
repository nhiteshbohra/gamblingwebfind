# gamblingwebfind

A high-performance, four-stage Python pipeline for discovering, classifying, and generating compliance reports for online gambling websites.

---

## How It Works — Big Picture

```
Stage 0: SearXNG Search          Stage 1: Common Crawl
(live web, Docker)                (historical parquet index)
         │                                  │
         └──────────────┬───────────────────┘
                        ▼
              MongoDB: domain_Listed
              { domain, active, processed:false, added_date }
                        │
                        ▼
              Stage 2: URL Checker
              • Fetches each domain's HTML
              • Matches against 500 gambling keywords
              • >= 3 matches → "gambling"
              • Stamps processed:true when done
                        │
                        ▼
              MongoDB: checked_domains
              { status, reason, checked_at, ... }
                        │  (gambling only)
                        ▼
              Stage 3: Screenshot Capture
              • Playwright headless Chromium
              • Full page-load wait (networkidle + scroll)
                        │
                        ▼
              output/
              ├── verify_results.xlsx
              ├── capture_report_<ts>.docx
              └── capture_results_<ts>.xlsx
```

---

## Prerequisites

- Python 3.11+
- MongoDB running locally on port 27017
- Docker Desktop (for Stage 0 — SearXNG)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/nhiteshbohra/gamblingwebfind.git
cd gamblingwebfind
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browser

```bash
playwright install chromium
```

> **Only if you enable `STEALTH_FALLBACK=true` in `.env`** (Cloudflare bypass):
> ```bash
> scrapling install
> ```

### 4. Set up `.env` configuration

Copy the example file and edit as needed:

```bash
copy .env.example .env
```

Minimum required `.env` (defaults work for local MongoDB):

```env
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_COLLECTION=domain_Listed
CHECKED_COLLECTION=checked_domains
```

All other values have sensible defaults — see `.env.example` for the full list.

### 5. Install Docker Desktop *(for Stage 0 only)*

Download from: https://www.docker.com/products/docker-desktop

> Stage 0 will auto-start Docker containers when you select Option 0 from the menu. You do NOT need to run `docker-compose` manually.

---

## Running the Project

```bash
python main.py
```

You will see the interactive menu:

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
Select an option (0-5):
```

---

## Menu Options — Detailed

### Option 0 — SearXNG Keyword Search *(Stage 0)*

Searches keywords on real search engines (Google, Bing, DuckDuckGo) via SearXNG running in Docker, extracts all result domains, and saves them to MongoDB.

**What happens automatically:**
1. Checks if SearXNG is already running on port 8080
2. If not → runs `docker-compose up -d` from `keywordssearch/` folder
3. Waits up to 30 seconds for containers to be ready
4. Searches your keywords across multiple pages
5. Saves new domains to `domain_Listed` with `processed: false`

**Usage:**
```
Select an option (0-5): 0

Enter keyword(s) to search (single or comma-separated, e.g. bet, casino, poker):
> casino, poker, betting, slot, spin
```

**Output:** New domains added to MongoDB `domain_Listed`

---

### Option 1 — Common Crawl Domain Fetch *(Stage 1)*

Searches the Common Crawl web archive (300M+ pages) for domains whose names contain your keywords. Performs DNS + HTTP probe on each domain to check if it's alive.

**Usage:**
```
Select an option (0-5): 1

Enter keyword(s) to search (single or comma-separated, e.g. bet, casino, slot, spin):
> bet, casino, slot, spin, win, play
```

**Output:** Domains written to `domain_Listed` with:
- `active: true` — reachable domain
- `active: false` — DNS failed / dead
- `active: "blocked"` — WAF / Cloudflare blocked
- `processed: false` — ready for Stage 2

> **Checkpoint system:** If interrupted, re-running with the same keywords resumes from the last completed batch automatically.

---

### Option 2 — Checking URL *(Stage 2)*

Fetches the HTML of every unprocessed active domain and classifies it as a gambling site or not.

**Filter:** Only picks up `domain_Listed` where `active: true` AND `processed != true`

**Classification rules:**
- **gambling** — 3 or more gambling keywords found in page HTML
- **regular** — page reachable but fewer than 3 keyword matches
- **blocked** — HTTP 403/429 or Cloudflare challenge detected
- **dead** — DNS failed, connection refused, or parked page

**After each domain is checked:**
- Result written to `checked_domains`
- Source document stamped `processed: true` — will never be re-checked

**Output:** `output/verify_results.xlsx` with 4 sheets:

| Sheet | Contents |
|-------|----------|
| Gambling | Confirmed gambling operators |
| Blocked | WAF / Cloudflare blocked |
| Dead | Unreachable / parked |
| Regular | Reachable but not gambling |

---

### Option 3 — Capture URL *(Stage 3)*

Takes full screenshots of every confirmed gambling domain and generates Word + Excel reports.

**Filter:** Only picks up `checked_domains` where `status: "gambling"` AND `screenshot_taken: false`

**Screenshot strategy (3 layers for complete pages):**
1. Navigate with progressive wait strategy (domcontentloaded → load → commit per retry)
2. `networkidle` wait — pauses until all XHR/fetch requests finish (catches lazy-loaded content)
3. Scroll to bottom → back to top — triggers viewport-lazy images
4. Settle delay — 1.0s → 1.5s → 2.0s per retry for slow sites

**Output:**
- `output/capture_report_<YYYY-MM-DD_HH-MM-SS>.docx` — Word document, 2 targets per page with clickable URLs and screenshots
- `output/capture_results_<YYYY-MM-DD_HH-MM-SS>.xlsx` — Excel workbook (Captured / Failed sheets)

> Temporary JPEG files are automatically deleted after being embedded into the Word report.

---

## Menu Options — Detailed

### Option 0 — SearXNG Keyword Search *(Stage 0)*

Searches keywords on real search engines (Google, Bing, DuckDuckGo) via SearXNG running in Docker, extracts all result domains, and saves them to MongoDB.

**What happens automatically:**
1. Checks if SearXNG is already running on port 8080
2. If not → runs `docker-compose up -d` from `keywordssearch/` folder
3. Waits up to 30 seconds for containers to be ready
4. Searches your keywords across multiple pages
5. Saves new domains to `domain_Listed` with `processed: false`

**Usage:**
```
Select an option (0-4): 0

Enter keyword(s) to search (single or comma-separated, e.g. bet, casino, poker):
> casino, poker, betting, slot, spin
```

**Output:** New domains added to MongoDB `domain_Listed`

---

### Option 1 — Common Crawl Domain Fetch *(Stage 1)*

Searches the Common Crawl web archive for domains whose names contain your keywords. Performs DNS + HTTP probe on each domain to check if it's alive.

**Usage:**
```
Select an option (0-4): 1

Enter keyword(s) to search (single or comma-separated, e.g. bet, casino, slot, spin):
> bet, casino, slot, spin, win, play
```

**Output:** Domains written to `domain_Listed` with:
- `active: true` — reachable domain
- `active: false` — DNS failed / dead
- `active: "blocked"` — WAF / Cloudflare blocked
- `processed: false` — ready for Stage 2

> **Checkpoint system:** If interrupted, re-running with the same keywords resumes from the last completed batch automatically.

---

### Option 2 — Checking URL *(Stage 2)*

Fetches the HTML of every unprocessed active domain and classifies it as a gambling site or not.

**Filter:** Only picks up `domain_Listed` where `active: true` AND `processed != true`

**Classification rules:**
- **gambling** — 3 or more gambling keywords found in page HTML
- **regular** — page reachable but fewer than 3 keyword matches
- **blocked** — HTTP 403/429 or Cloudflare challenge detected
- **dead** — DNS failed, connection refused, or parked lander

**Deep Crawl Link Extraction:**
When a page is classified as `gambling` and contains 3+ outbound links, `checking_url/deep_crawl.py` extracts external domain links (filtering out social media, ad networks, and same-domain links) and seeds them back into `domain_Listed` with `source: "deep_crawl"` for future checking.

**After each domain is checked:**
- Result written to `checked_domains`
- Source document stamped `processed: true` — will never be re-checked

**Output:** `output/verify_results.xlsx` with 4 sheets (Gambling, Blocked, Dead, Regular) featuring clickable `https://` hyperlinks.

---

### Option 3 — Capture URL *(Stage 3)*

Takes full screenshots of confirmed gambling domains with live double-layer verification, and generates Word (.docx), PDF (.pdf), and Excel (.xlsx) reports.

**Filter:** Only picks up `checked_domains` where `status: "gambling"` AND `screenshot_taken: false`

**Double-Layer Verification Strategy:**
1. **Layer 1 (Status & 403 WAF Check)**: Evaluates response codes (403, 429, 404, 500+) and text markers.
2. **Layer 2 (Live Keyword Re-check)**: Classifies rendered HTML in browser memory (reclassifies non-gambling as `regular`).
3. **Layer 3 (Screenshot & Clean Save)**: Saves screenshots only for confirmed sites, deletes temporary files automatically.

**Export Format Options:**
When running Option 3, you can choose:
1. **Single Combined Files** *(default)*: 1 Master Word document, 1 Master PDF, and 1 Master Excel workbook.
2. **Batched Deliverables**: Splits domains into batch folders (e.g. `batch_001`, `batch_002` of 20, 40, or 50 items each).



---

### Option 4 — Both Checking & Capture

Runs Stage 2 then Stage 3 back-to-back automatically.

---

## Project File Structure

```
gamblingwebfind/
│
├── .env                              ← Your config (not committed to git)
├── .env.example                      ← Template with all variables
├── main.py                           ← Entry point — interactive menu
├── gambling_top_500_keywords.json    ← 500 gambling signal keywords
├── requirements.txt                  ← Python dependencies
│
├── keywordssearch/                   ← Stage 0 — SearXNG web search
│   ├── searxng_search.py             ← Docker auto-start + search + save to MongoDB
│   ├── docker-compose.yml            ← Redis + SearXNG container definitions
│   └── searxng/
│       └── settings.yml              ← SearXNG config (JSON API, web engines only)
│
├── keywordsindomainfetch/            ← Stage 1 — Common Crawl domain extraction
│   ├── find_domains.py               ← DuckDB query → DNS probe → MongoDB upsert
│   └── manifests/                    ← Cached CC crawl manifest files
│
├── db/                               ← Shared MongoDB layer
│   └── mongo_client.py               ← All DB access: connections, queries, write_result(), seed_discovered_domains()
│
├── checking_url/                     ← Stage 2 — URL fetching & classification
│   ├── runner.py                     ← Orchestrator (async fetch + classification + deep crawl hook)
│   ├── fetcher.py                    ← Two-tier HTTP fetcher (Fast AsyncFetcher + StealthyFetcher)
│   ├── classifier.py                 ← Keyword matcher & parked lander detector
│   └── deep_crawl.py                 ← Aggregator outbound link extractor
│
├── capture_url/                      ← Stage 3 — Screenshot capture & reporting
│   ├── runner.py                     ← Orchestrator (Playwright BrowserPool)
│   ├── screenshot.py                 ← Page capture with full-load strategy
│   ├── excel_exporter.py             ← MongoDB → Excel workbooks (clickable links)
│   └── docx_report_generator.py      ← MongoDB → Word/PDF reports (2 per page, clickable links)
│
└── project_sup/                      ← Project support & helper scripts
    └── helping_code/
        ├── audit_db.py               ← Database health check & interactive/automated cleanup
        ├── cleanup_db.py             ← Wrapper delegating to audit_db.py --fix
        ├── compare_dbs.py            ← Interactive tool to compare & sync domains between two DBs
        ├── compare_processed_domains.py.py ← Syncs processed=True flag for domains in checked_domains
        ├── batch_splitter.py         ← Splits CSV and PDF reports into batch subfolders
        ├── import_output_excel.py    ← Bulk import existing Excel data into MongoDB
        ├── merge_pdfs.py             ← PDF merger tool
        └── arrange_docx_report.py    ← Re-arrange existing Word report (2 per page, batched)
```

---

## MongoDB Collections & Schema Design

### `domain_Listed` — Source of domains for Stage 2

| Field | Type | Description |
|-------|------|-------------|
| `_id` | string | Domain name — primary key |
| `domain` | string | e.g. `"bet365.com"` |
| `active` | bool/string | `true` / `false` / `"blocked"` |
| `processed` | bool | `false` on insert → `true` after Stage 2 checks it |
| `added_date` | string | First import date `YYYY-MM-DD` — set once via `$setOnInsert` |
| `source` | string | Origin e.g. `"searxng_search"`, `"common_crawl"`, `"deep_crawl"` |
| `discovered_from` | string | Aggregator domain that contained the link (optional) |

### `checked_domains` — Minimal classification results

| Field | Type | Present On | Description |
|-------|------|------------|-------------|
| `_id` | string | All docs | Domain name — primary key |
| `domain` | string | All docs | Same as `_id` |
| `url` | string | All docs | Full URL e.g. `"https://bet365.com"` |
| `status` | string | All docs | `"gambling"` / `"regular"` / `"blocked"` / `"dead"` |
| `reason` | array | All docs | Matched keyword signals |
| `added_date` | string | All docs | First import date `YYYY-MM-DD` |
| `screenshot_taken` | bool | `gambling` only | `true` after successful screenshot |
| `screenshot_failed_reason` | string | `gambling` only | Error reason if capture failed, else `null` |
| `exported` | bool | Exported docs only | `true` once included in an exported report |
| `exported_at` | string | Exported docs only | Export date `YYYY-MM-DD` |

---

## Utility & Support Scripts

These standalone tools are located in `project_sup/helping_code/` and run directly.

### Database Health Check & Schema Cleanup

Audits MongoDB collections for bloat or schema violations and interactively offers to repair them:

```bash
python project_sup/helping_code/audit_db.py
```

To run non-interactively in automated pipelines:

```bash
python project_sup/helping_code/audit_db.py --fix
# OR
python project_sup/helping_code/cleanup_db.py
```

---

### Compare Databases & Sync Missing Domains

Interactively compares two MongoDB databases (e.g. `gamblingsitetry` vs `gamblingsites`) and asks whether to copy missing domains in either direction:

```bash
python project_sup/helping_code/compare_dbs.py
```

---

### Sync Processed Flags for Existing Results

Scans `domain_Listed` against `checked_domains` and updates `processed: true` for any domains that were already classified:

```bash
python project_sup/helping_code/compare_processed_domains.py.py
```

---

### Batch Splitter (CSV + PDF Reports)

Splits a combined CSV report and matching PDF report into smaller standalone batch folders (e.g., 20 items per batch):

```bash
python project_sup/helping_code/batch_splitter.py --csv report.csv --pdf report.pdf --size 20
```

---

### Import Existing Excel Data into MongoDB

Imports existing classified Excel workbooks into MongoDB (`domain_Listed` with `processed: true` and `checked_domains`):

```bash
python project_sup/helping_code/import_output_excel.py
```

---

### Re-arrange Word Reports

Re-formats existing Word reports to 2 items per page with clickable hyperlinks:

```bash
python project_sup/helping_code/arrange_docx_report.py --csv domains.csv --docx report.docx --output-dir ./output
```

---

## `.env` — Configuration Variables

```env
# ── Stage 0 — SearXNG ──────────────────────────────────────────────────────────
SEARXNG_BASE_URL=http://127.0.0.1:8080
SEARXNG_MAX_PAGES=4
SEARXNG_PAGE_DELAY=1.0
SEARXNG_TIMEOUT=10.0

# ── MongoDB ────────────────────────────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_DB2_NAME=gamblingsitetry
MONGO_COLLECTION=domain_Listed
CHECKED_COLLECTION=checked_domains

# ── Stage 1 — Common Crawl ────────────────────────────────────────────────────
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
PARQUET_BATCH_SIZE=20
EXPORT_TO_MONGO=true

# ── Stage 2 — URL Checking ────────────────────────────────────────────────────
FETCH_TIMEOUT=10
PER_DOMAIN_DELAY=2.0
MAX_CONCURRENT_FETCHES=20

STEALTH_FALLBACK=false
STEALTH_TIMEOUT=60
STEALTH_CONCURRENCY=3

# ── Deep Crawl Link Extraction ────────────────────────────────────────────────
DEEP_CRAWL_MIN_LINKS=3
DEEP_CRAWL_STRICT=false

# ── Stage 3 — Screenshots ─────────────────────────────────────────────────────
SCREENSHOT_CONCURRENCY=15
```

---

## Key Design Principles

| Principle | Why |
|-----------|-----|
| `_id` = domain name | Primary key is domain string — no redundant indexes |
| `processed` flag on source docs | Prevents Stage 2 from re-checking domains across runs |
| Deep Crawl aggregator extraction | Outbound links on gambling pages are captured and queued |
| Active `https://` hyperlinks | All URLs in Excel, Word, and PDF reports are directly clickable |
| Minimal Schema Design | Eliminates database bloat (`checked_at`, `last_updated_at` removed) |
| Unified `.env` Config | Single `.env` file drives all pipeline stages and helper scripts |

