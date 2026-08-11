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
│  Output: gamblingsites.domain_Listed  { domain, active: true/false }│
└─────────────────────────┬───────────────────────────────────────────┘
                          │  active: true domains
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 2 — python main.py --mode check                              │
│                                                                     │
│  Input : domain_Listed WHERE active = true                          │
│          (skips domains already in checked_domains)                 │
│  Method: aiohttp fetch -> BeautifulSoup keyword matching            │
│          Keywords loaded from gambling_top_500_keywords.json        │
│          Rule: >= 3 matching keywords -> status = "gambling"        │
│  Output: gamblingsites.checked_domains                              │
│                                                                     │
│  Status values:                                                     │
│    gambling  — confirmed gambling operator (>= 3 keyword matches)   │
│    regular   — reachable, not gambling (< 3 keyword matches)        │
│    blocked   — 403/429 or Cloudflare WAF challenge                   │
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
│  Output: output/capture_report.docx   (2 targets per page)          │
│          output/capture_results.xlsx  (Captured / Failed sheets)    │
│          (Temporary JPEGs in output/screenshots/ are auto-deleted)  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## MongoDB — Database: `gamblingsites`

### 1. `domain_Listed` — Stage 1 output (READ-ONLY by Stage 2)

| Field    | Type        | Description                                           |
|----------|-------------|-------------------------------------------------------|
| `_id`    | ObjectId    | Auto-generated                                        |
| `domain` | string      | e.g. `"bet365.com"`                                   |
| `active` | bool/string | `true` = reachable, `false` = dead, `"blocked"` = WAF |

### 2. `checked_domains` — Stage 2/3 output (Strict 7-Field Schema)

Each document strictly contains ONLY these 7 fields:

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
  "screenshot_failed_reason": null
}
```

| Field                      | Type   | Description                                       |
|----------------------------|--------|---------------------------------------------------|
| `_id`                      | string | domain name (primary key)                         |
| `domain`                   | string | same as `_id`                                     |
| `url`                      | string | full normalized URL                               |
| `status`                   | string | `"gambling"`, `"regular"`, `"blocked"`, `"dead"`  |
| `reason`                   | array  | matched signal keywords from the 500 keyword list |
| `screenshot_taken`         | bool   | `true` after successful screenshot capture        |
| `screenshot_failed_reason` | string | error message if capture failed, else `null`      |

---

## Unified Output Directory Structure

All generated reports land in a single root **`output/`** folder:

```
gamblingwebfind/
└── output/
    ├── capture_report.docx   <- Word report (2 targets per page with hyperlinks)
    ├── capture_results.xlsx  <- Excel workbook (Captured / Failed sheets: S.No., Domain, URL)
    └── verify_results.xlsx   <- Excel workbook (4 sheets: Gambling / Blocked / Dead / Regular)
```

---

## File Structure

```
gamblingwebfind/
├── .env                            <- Single config for all 3 stages
├── main.py                         <- Entry point: --mode check|capture|both
├── gambling_top_500_keywords.json  <- 500 keyword signal terms
├── seed_500_keywords.py            <- Seeder script for MongoDB keywords collection
├── import_output_excel.py          <- Bulk seeder for output.xlsx into MongoDB
│
├── keywordsindomainfetch/          <- Stage 1
│   ├── find_domains.py             <- Common Crawl -> domain_Listed
│   └── manifests/                  <- CC crawl manifest cache
│
├── db/
│   └── mongo_client.py             <- Connection, collection accessors, URL helpers
│
├── checking_url/                   <- Stage 2
│   ├── fetcher.py                  <- aiohttp fetch with retry + failure classification
│   ├── classifier.py               <- Matches page HTML against 500 keywords (>= 3 matches)
│   └── runner.py                   <- Orchestrates fetch -> classify -> write
│
├── capture_url/                    <- Stage 3
│   ├── screenshot.py               <- Playwright BrowserPool, JPEG encode, validation
│   └── runner.py                   <- Orchestrates capture -> write to checked_domains
│
└── reports/
    ├── excel_exporter.py           <- Mongo -> Excel workbooks (clean S.No., Domain, URL columns)
    └── docx_report_generator.py    <- Mongo -> Word doc (2 per page) + auto-deletes temp JPEGs
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

### Stage 1 — Discover domains from Common Crawl
```bash
cd keywordsindomainfetch
python find_domains.py --keyword bet casino slot spin win play 777
```

### Stage 2 — Classify active domains
```bash
python main.py --mode check
python main.py --mode check --limit 50   # test run with 50 domains
```
Outputs `output/verify_results.xlsx` (4 sheets: Gambling / Blocked / Dead / Regular).

### Stage 3 — Screenshot gambling sites & generate reports
```bash
python main.py --mode capture
python main.py --mode capture --limit 20  # test run with 20 domains
```
Outputs `output/capture_report.docx` (2 targets per page) and `output/capture_results.xlsx` (Captured / Failed sheets). Auto-deletes temporary `.jpg` files when done.

### Run Stage 2 + Stage 3 together
```bash
python main.py --mode both
```

### Import existing output.xlsx dataset into MongoDB
```bash
python import_output_excel.py
```

---

## Key Architectural Principles

1. **Single `.env` Config:** One central configuration file for all three pipeline stages.
2. **Strict 7-Field MongoDB Schema:** No bloated audit timestamps or redundant metadata in `checked_domains`.
3. **$\ge 3$ Keyword Rule:** Website is marked `status: "gambling"` if 3 or more keywords match page HTML. Hard excludes `.gov` and `.edu`.
4. **Single `output/` Directory:** All Word documents and Excel workbooks land in one clean folder.
5. **Auto-Cleanup:** Screenshot JPEGs are embedded into `capture_report.docx` and immediately deleted from disk to prevent double memory/storage consumption.
6. **1-to-1 Word & Excel Alignment:** `capture_report.docx` and `capture_results.xlsx` pull the exact same set of captured domains from MongoDB simultaneously.
