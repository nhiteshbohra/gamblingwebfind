# gamblingwebfind

A high-performance, three-stage Python pipeline for discovering, classifying, and generating compliance reports for online gambling operators.

---

## Architecture Overview

```
                      Common Crawl (WARC Index)
                                  │
                                  ▼
           Stage 1: Domain Finder (DuckDB SQL Query)
                                  │
                                  ▼
                 MongoDB: gamblingsites.domain_Listed
                          (active: true / false / "blocked")
                                  │
                                  ▼
           Stage 2: URL Checker & Classifier (aiohttp)
                    (>= 3 keyword matches -> gambling)
                                  │
                                  ▼
                 MongoDB: gamblingsites.checked_domains
                          (Strict 7-field schema)
                                  │
                                  ▼
           Stage 3: Screenshot Capturer (Playwright Chromium)
                                  │
                                  ▼
                   Root output/ Folder:
                   ├── capture_report.docx   (Word Doc)
                   ├── capture_results.xlsx  (Excel Doc)
                   └── verify_results.xlsx   (Verification Excel)
```

---

## Key Features

- **Stage 1 (Domain Finder):** Uses DuckDB SQL queries over Common Crawl Parquet indices to extract 10,000s of domains matching gambling search phrases without hitting search engine CAPTCHAs.
- **Stage 2 (URL Checker & Classifier):** Asynchronously fetches live HTML via `aiohttp` and classifies domains against `gambling_top_500_keywords.json`. Websites with **$\ge 3$ matching keywords** are classified as `gambling`.
- **Stage 3 (Screenshot & Reporting):** Takes headless Playwright Chromium screenshots for all verified gambling sites, formats them into a compact A4 Word report (`capture_report.docx`, 2 targets per page with clickable hyperlinks), and exports matching Excel workbooks (`capture_results.xlsx`).
- **Auto-Cleanup:** Temporary JPEG files are automatically deleted from disk after being embedded into the Word report to prevent duplicate disk storage.
- **Single Output Folder:** All final reports land cleanly in a single `output/` folder at project root.

---

## Installation

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/nhiteshbohra/gamblingwebfind.git
cd gamblingwebfind

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment (`.env`)
Create a `.env` file at the root of the project (or use defaults):

```env
# MongoDB Connection
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gamblingsites
MONGO_COLLECTION=domain_Listed
CHECKED_COLLECTION=checked_domains
KEYWORDS_COLLECTION=keywords

# Stage 1 Tuning
MAX_WORKERS=200
TIMEOUT=5.0
MONGO_BATCH_SIZE=1000
PARQUET_BATCH_SIZE=20
EXPORT_TO_MONGO=true

# Stage 2 Tuning
FETCH_TIMEOUT=10
PER_DOMAIN_DELAY=2.0
MAX_CONCURRENT_FETCHES=20

# Stage 3 Tuning
SCREENSHOT_CONCURRENCY=15
```

---

## Usage Guide

### 1. Stage 1 — Discover Domains from Common Crawl
```bash
cd keywordsindomainfetch
python find_domains.py --keyword bet casino slot spin win play 777
```
*Results land in MongoDB `gamblingsites.domain_Listed`.*

### 2. Stage 2 — Check & Classify Active Domains
```bash
# Process all active domains
python main.py --mode check

# Test run with first 50 domains
python main.py --mode check --limit 50
```
*Outputs `output/verify_results.xlsx` (4 sheets: Gambling / Blocked / Dead / Regular).*

### 3. Stage 3 — Capture Screenshots & Generate Reports
```bash
# Capture screenshots for all verified gambling sites
python main.py --mode capture

# Test run with first 20 gambling sites
python main.py --mode capture --limit 20
```
*Outputs `output/capture_report.docx` and `output/capture_results.xlsx`. Temporary `.jpg` files are auto-cleaned from disk.*

### 4. Run Stage 2 & Stage 3 Together
```bash
python main.py --mode both
```

### 5. Bulk Import Output Excel (`output.xlsx`)
To populate MongoDB from an existing `output.xlsx` dataset:
```bash
python import_output_excel.py
```

---

## MongoDB Document Schema (`checked_domains`)

Each document in `checked_domains` strictly contains 7 fields:

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

---

## Project Structure

```
gamblingwebfind/
├── .env                            <- Single config for all 3 stages
├── ARCHITECTURE.md                 <- Technical Architecture & Design Document
├── README.md                       <- Setup & Usage Guide
├── main.py                         <- Unified CLI entry point (--mode check|capture|both)
├── gambling_top_500_keywords.json  <- 500 keyword signal terms
├── import_output_excel.py          <- Bulk importer for output.xlsx
│
├── keywordsindomainfetch/          <- Stage 1: Common Crawl Domain Extraction
│   ├── find_domains.py
│   └── manifests/
│
├── db/                             <- MongoDB client & accessors
│   └── mongo_client.py
│
├── checking_url/                   <- Stage 2: Fetch & Classification
│   ├── fetcher.py
│   ├── classifier.py
│   └── runner.py
│
├── capture_url/                    <- Stage 3: Screenshot Capture
│   ├── screenshot.py
│   └── runner.py
│
└── reports/                        <- Exporters & Word/Excel Builders
    ├── excel_exporter.py
    └── docx_report_generator.py
```
