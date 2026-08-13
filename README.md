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
│   ├── searxng_search.py             ← Core: Docker auto-start + search + save to MongoDB
│   ├── docker-compose.yml            ← Redis + SearXNG container definitions
│   └── searxng/
│       └── settings.yml              ← SearXNG config (JSON API, web engines only)
│
├── keywordsindomainfetch/            ← Stage 1 — Common Crawl domain extraction
│   ├── find_domains.py               ← DuckDB query → DNS probe → MongoDB upsert
│   └── manifests/                    ← Cached CC crawl manifest files
│
├── db/                               ← Shared MongoDB layer
│   └── mongo_client.py               ← All DB access: connections, queries, write_result()
│                                        _id is the only index (domain name = primary key)
│
├── checking_url/                     ← Stage 2 — URL fetching & classification
│   ├── runner.py                     ← Orchestrator (asyncio, semaphore concurrency)
│   ├── fetcher.py                    ← Two-tier HTTP fetcher (fast + stealth fallback)
│   └── classifier.py                 ← Keyword matcher (BeautifulSoup text extraction)
│
├── capture_url/                      ← Stage 3 — Screenshot capture & reporting
│   ├── runner.py                     ← Orchestrator (Playwright BrowserPool)
│   ├── screenshot.py                 ← Page capture with full-load strategy
│   ├── excel_exporter.py             ← MongoDB → Excel workbooks
│   └── docx_report_generator.py      ← MongoDB → Word report (2 per page)
│
└── project_sup/                      ← Manual utility scripts
    └── mongodbupdate/
        ├── import_output_excel.py    ← Bulk import existing Excel data into MongoDB
        ├── update_export_status.py   ← Mark domains as exported in MongoDB
        └── arrange_docx_report.py    ← Re-arrange existing Word report (2 per page, batched)
```

---

## MongoDB Collections

### `domain_Listed` — Source of domains for Stage 2

| Field | Type | Description |
|-------|------|-------------|
| `_id` | string | Domain name — primary key + auto-indexed |
| `domain` | string | e.g. `"bet365.com"` |
| `active` | bool/string | `true` / `false` / `"blocked"` |
| `processed` | bool | `false` on insert → `true` after Stage 2 checks it |
| `added_date` | string | First import date `YYYY-MM-DD` — set once, never overwritten |

### `checked_domains` — Classification results

| Field | Type | Description |
|-------|------|-------------|
| `_id` | string | Domain name — primary key + auto-indexed |
| `domain` | string | Same as `_id` |
| `url` | string | Full URL e.g. `"https://bet365.com"` |
| `status` | string | `"gambling"` / `"regular"` / `"blocked"` / `"dead"` |
| `reason` | array | Matched keyword signals |
| `checked_at` | string | Timestamp in IST e.g. `"2026-08-13 19:45:00 IST"` |
| `screenshot_taken` | bool | `true` after a successful screenshot |
| `screenshot_failed_reason` | string | Error reason if capture failed, else `null` |
| `added_date` | string | First import date — `YYYY-MM-DD` |
| `exported` | bool | `true` once included in a report |
| `exported_at` | string | Export date `YYYY-MM-DD` |

---

## Utility Scripts

These are standalone scripts run directly — not through `main.py`.

### Import existing Excel data into MongoDB

If you have an existing Excel file with domains already classified:

```bash
cd project_sup/mongodbupdate
python import_output_excel.py
```

Reads sheets: **Verified / Rejected / Dead / Blocked**  
Writes to both `domain_Listed` (with `processed: true`) and `checked_domains`.  
Uses `$setOnInsert` — never overwrites existing records.

---

### Seed domains from CSV

```bash
python main.py --seed path/to/domains.csv
```

CSV must have a `domain` or `url` column. All domains inserted with `active: true`.

---

### Mark domains as exported

```bash
cd project_sup/mongodbupdate
python update_export_status.py path/to/exported_domains.csv
```

Updates `exported: true`, `export_status`, `exported_at`, `screenshot_taken` in MongoDB.

---

### Re-arrange an existing Word report

```bash
cd project_sup/mongodbupdate
python arrange_docx_report.py --csv domains.csv --docx report.docx --output-dir ./output
```

Useful when you have an existing report that needs to be reformatted to 2 targets per page, or split into smaller batch files (e.g. 1000 targets per file).

---

## `.env` — All Configuration Variables

```env
# ── Stage 0 — SearXNG (Docker auto-started) ───────────────────────────────────
SEARXNG_BASE_URL=http://127.0.0.1:8080
SEARXNG_MAX_PAGES=4         # Pages of results per keyword (10 results/page)
SEARXNG_PAGE_DELAY=1.0      # Delay between pages (seconds)
SEARXNG_TIMEOUT=10.0        # HTTP timeout per request (seconds)

# ── MongoDB ────────────────────────────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_COLLECTION=domain_Listed        # Stage 0/1 output — Stage 2 source
CHECKED_COLLECTION=checked_domains    # Stage 2/3 output
KEYWORDS_COLLECTION=keywords

# ── Stage 1 — Common Crawl ────────────────────────────────────────────────────
MAX_WORKERS=200             # DNS/HTTP concurrent connections
TIMEOUT=5.0                 # HTTP probe timeout (seconds)
MONGO_BATCH_SIZE=1000       # Domains per MongoDB bulk write
PARQUET_BATCH_SIZE=20       # Parquet files per DuckDB batch
EXPORT_TO_MONGO=true        # Set false to dry-run without saving

# ── Stage 2 — URL Checking ────────────────────────────────────────────────────
FETCH_TIMEOUT=10            # HTTP fetch timeout per domain (seconds)
PER_DOMAIN_DELAY=2.0        # Min seconds between re-fetching same domain
MAX_CONCURRENT_FETCHES=20   # Max simultaneous fetches

STEALTH_FALLBACK=false      # true = retry blocked domains with real browser
STEALTH_TIMEOUT=60          # Browser timeout for Cloudflare solving (seconds)
STEALTH_CONCURRENCY=3       # Max simultaneous stealth browser instances

# ── Stage 3 — Screenshots ─────────────────────────────────────────────────────
SCREENSHOT_CONCURRENCY=15   # Max simultaneous Playwright browser contexts
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| `_id` = domain name | Domain is already unique — no separate index needed |
| `processed` flag on source docs | Prevents Stage 2 re-checking domains across runs |
| `$ne: true` filter | Legacy docs without `processed` field are treated as unprocessed — backward compatible |
| `write_result()` atomically sets `processed:true` | No crash window between writing result and marking done |
| `$setOnInsert` for `added_date` | First import date is preserved — never overwritten on re-import |
| networkidle wait in screenshots | Captures pages after all XHR/API calls finish, not just HTML load |
| Docker auto-start in Stage 0 | User never needs to manually run `docker-compose` |
