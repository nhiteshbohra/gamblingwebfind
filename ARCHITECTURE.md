# gamblingwebfind — Architecture

## Overview

Three-stage pipeline for discovering, classifying, and documenting gambling websites.
All stages share a single MongoDB instance (`gamblingsites`) and a single `.env` configuration file at the project root.

---

## Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1 — keywordsindomainfetch/find_domains.py                    │
│                                                                     │
│  Input : Common Crawl Parquet index (CC-MAIN-2024-22)              │
│  Method: DuckDB SQL query over keyword list                         │
│  Output: gamblingsites.domain_Listed  { domain, active, added_date }│
└─────────────────────────┬───────────────────────────────────────────┘
                          │  active: true domains
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 2 — python main.py --mode check                              │
│                                                                     │
│  Input : domain_Listed WHERE active = true                          │
│          (skips domains already in checked_domains)                 │
│  Method: Scrapling AsyncFetcher (curl_cffi impersonation) fetch,    │
│          escalating to StealthyFetcher (solve_cloudflare=True)      │
│          on 'blocked' verdicts if STEALTH_FALLBACK=true             │
│          -> BeautifulSoup keyword matching                          │
│          Keywords loaded from gambling_top_500_keywords.json        │
│          Rule: >= 3 matching keywords -> status = "gambling"        │
│  Output: gamblingsites.checked_domains                              │
│                                                                     │
│  Status values:                                                     │
│    gambling  — confirmed gambling operator (>= 3 keyword matches)   │
│    regular   — reachable, not gambling (< 3 keyword matches)        │
│    blocked   — 403/429 or Cloudflare WAF challenge                  │
│    dead      — DNS fail / connection refused / parked               │
│                                                                     │
│  Export: output/verify_results.xlsx (4 sheets)                      │
│          Gambling | Blocked | Dead | Regular                        │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  status = "gambling" AND screenshot_taken = false
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 3 — python main.py --mode capture                            │
│                                                                     │
│  Input : checked_domains WHERE status=gambling, screenshot_taken=false│
│  Method: Playwright headless Chromium, 1280x720 JPEG q68           │
│  Output: output/capture_report_<date>.docx   (2 targets per page)  │
│          output/capture_results_<date>.xlsx  (Captured / Failed)   │
│          (Temporary JPEGs in output/screenshots/ are auto-deleted)  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## MongoDB — Database: `gamblingsites`

### 1. `domain_Listed` — Stage 1 output (READ-ONLY by Stage 2)

| Field        | Type        | Description                                            |
|--------------|-------------|--------------------------------------------------------|
| `_id`        | string      | domain name (primary key)                              |
| `domain`     | string      | e.g. `"bet365.com"`                                    |
| `active`     | bool/string | `true` = reachable, `false` = dead, `"blocked"` = WAF  |
| `added_date` | string      | Date domain was first imported — `YYYY-MM-DD` (set once, never overwritten) |

### 2. `checked_domains` — Stage 2/3 output

Each document contains these fields:

```json
{
  "_id": "we-play.poker",
  "domain": "we-play.poker",
  "url": "https://we-play.poker",
  "status": "gambling",
  "reason": [
    "play now",
    "cashier",
    "casino",
    "poker",
    "wager",
    "responsible gambling"
  ],
  "screenshot_taken": true,
  "screenshot_failed_reason": null,
  "added_date": "2026-08-13",
  "exported": true,
  "exported_at": "2026-08-13"
}
```

| Field                      | Type   | Description                                                    |
|----------------------------|--------|----------------------------------------------------------------|
| `_id`                      | string | domain name (primary key)                                      |
| `domain`                   | string | same as `_id`                                                  |
| `url`                      | string | full normalized URL                                            |
| `status`                   | string | `"gambling"`, `"regular"`, `"blocked"`, `"dead"`               |
| `reason`                   | array  | matched signal keywords from the 500 keyword list              |
| `screenshot_taken`         | bool   | `true` after successful screenshot capture                     |
| `screenshot_failed_reason` | string | error message if capture failed, else `null`                   |
| `added_date`               | string | Date domain was first imported — `YYYY-MM-DD` (set once via `$setOnInsert`, never overwritten) |
| `exported`                 | bool   | `true` once included in an exported report                     |
| `exported_at`              | string | Date of export — `YYYY-MM-DD` (date only, no time)             |

> **Note:** `captured_at` has been removed from the schema. Export tracking uses `exported_at` (date only). Import tracking uses `added_date` (date only, set once).

---

## Unified Output Directory Structure

All generated reports land in a single root **`output/`** folder:

```
gamblingwebfind/
└── output/
    ├── capture_report_<YYYY-MM-DD_HH-MM-SS>.docx   <- Word report (2 targets per page with hyperlinks)
    ├── capture_results_<YYYY-MM-DD_HH-MM-SS>.xlsx  <- Excel workbook (Captured / Failed sheets: S.No., Domain, URL)
    └── verify_results.xlsx                          <- Excel workbook (4 sheets: Gambling / Blocked / Dead / Regular)
```

---

## File Structure

```
gamblingwebfind/
├── .env                            <- Single config for all 3 stages
├── main.py                         <- Entry point: --mode check|capture|both
├── gambling_top_500_keywords.json  <- 500 keyword signal terms
├── seed_500_keywords.py            <- Seeder script for MongoDB keywords collection
│
├── keywordsindomainfetch/          <- Stage 1
│   ├── find_domains.py             <- Common Crawl -> domain_Listed (stamps added_date)
│   └── manifests/                  <- CC crawl manifest cache
│
├── db/
│   └── mongo_client.py             <- Connection, collection accessors, URL helpers, seed_from_csv
│
├── checking_url/                   <- Stage 2
│   ├── fetcher.py                  <- aiohttp fetch with retry + failure classification
│   ├── classifier.py               <- Matches page HTML against 500 keywords (>= 3 matches)
│   └── runner.py                   <- Orchestrates fetch -> classify -> write
│
├── capture_url/                    <- Stage 3 & Reporting
│   ├── screenshot.py               <- Playwright BrowserPool, JPEG encode, validation
│   ├── runner.py                   <- Orchestrates capture -> write to checked_domains
│   ├── excel_exporter.py           <- Mongo -> Excel workbooks (S.No., Domain, URL columns; exported_at date-only)
│   └── docx_report_generator.py    <- Mongo -> Word doc (2 per page) + auto-deletes temp JPEGs
│
└── project_sup/
    └── mongodbupdate/
        ├── import_output_excel.py  <- Bulk Excel importer (stamps added_date on new domains)
        └── update_export_status.py <- Utility to update export status in MongoDB
```

---

## Configuration — `.env`

```env
# MongoDB Connection
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_COLLECTION=domain_Listed        # Stage 1 output / Stage 2 source
CHECKED_COLLECTION=checked_domains    # Stage 2/3 output
KEYWORDS_COLLECTION=keywords          # Signal terms collection

# Stage 1 tuning
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
PARQUET_BATCH_SIZE=20
EXPORT_TO_MONGO=true

# Stage 2 tuning
FETCH_TIMEOUT=10
PER_DOMAIN_DELAY=2.0
MAX_CONCURRENT_FETCHES=20

# Stage 3 tuning
SCREENSHOT_CONCURRENCY=15
```

---

## How to Run

Simply run `python main.py` to launch the interactive prompt menu:
```bash
python main.py
```
Select from:
1. **Keywords Search in Domain Fetch (Stage 1)** — prompt for keywords (e.g. `bet, casino, slot`)
2. **Checking URL (Stage 2)** — process unchecked domains in DB
3. **Capture URL (Stage 3)** — capture screenshots & build reports
4. **Both Checking & Capture URL** — execute Stage 2 + Stage 3 sequentially
5. **Exit**

---

### Direct Script Execution

#### Stage 1 — Discover domains directly from Common Crawl
```bash
cd keywordsindomainfetch
python find_domains.py --keyword bet casino slot spin win play 777
```


### Bulk import from Excel dataset
```bash
cd project_sup/mongodbupdate
python import_output_excel.py
```

### Seed domains from CSV
```bash
python main.py --seed path/to/domains.csv
```

---

## Key Architectural Principles

1. **Single `.env` Config:** One central configuration file for all three pipeline stages.
2. **`added_date` Tracking:** Every domain gets stamped with the date it was first imported (`YYYY-MM-DD`), set once via MongoDB `$setOnInsert` — never overwritten on re-import.
3. **`exported_at` Date-Only:** Export date is stored as `YYYY-MM-DD` (no time component) for clean reporting.
4. **No `captured_at`:** Capture timestamps have been removed; the pipeline only tracks import date and export date.
5. **$\ge 3$ Keyword Rule:** Website is marked `status: "gambling"` if 3 or more keywords match page HTML. Hard excludes `.gov` and `.edu`.
6. **Single `output/` Directory:** All Word documents and Excel workbooks land in one clean folder.
7. **Auto-Cleanup:** Screenshot JPEGs are embedded into `capture_report.docx` and immediately deleted from disk to prevent double memory/storage consumption.
8. **1-to-1 Word & Excel Alignment:** `capture_report.docx` and `capture_results.xlsx` pull the exact same set of captured domains from MongoDB simultaneously.
