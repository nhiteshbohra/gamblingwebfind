# GamblingWebFind — Test Suite

Comprehensive, offline, zero-network-dependency automated test suite for the **GamblingWebFind** pipeline and FastAPI web service.

---

## 1. Quick Start

Run the entire test suite:
```bash
python -m pytest
```

Run tests with verbose output:
```bash
python -m pytest -v
```

Run with test coverage breakdown:
```bash
python -m pytest --cov=. --cov-report=term-missing
```

Run tests for a specific stage:
```bash
python -m pytest tests/test_stage0_keywordssearch.py -v
python -m pytest tests/test_stage1_domain_fetch.py -v
python -m pytest tests/test_stage2_checking_url.py -v
python -m pytest tests/test_stage3_export_domains.py -v
python -m pytest tests/test_helpers_and_import.py -v
python -m pytest tests/test_api.py -v
python -m pytest tests/test_end_to_end.py -v
```

---

## 2. Test Architecture & Isolation

All tests run **100% offline** without requiring a running MongoDB instance, Docker daemon, Ollama instance, or Playwright browser:

- **MongoDB Isolation (`tests/conftest.py`)**:
  - Uses `mongomock` in-memory database patched at `pymongo.MongoClient` and `db.mongo_client._client` / `_db`.
  - Every test receives a clean, isolated database that resets automatically after test teardown.
- **Filesystem Isolation (`isolated_env`)**:
  - Automatically redirects `OUTPUT_DIR` and `SCREENSHOT_DIR` to temporary pytest directories (`tmp_path`).
  - Zero pollution of the repo's `output/` directory.
- **Service Mocking**:
  - **Stage 0**: Search engines (DuckDuckGo, Yahoo, SearXNG) are mocked with realistic HTML SERP results.
  - **Stage 1**: Common Crawl / Deep Crawl APIs and local file loaders (CSV, TXT, XLSX) tested with synthetic datasets.
  - **Stage 2**: `aiohttp` web fetching and Ollama AI challenge classifications mocked via deterministic fixtures.
  - **Stage 3**: `BrowserPool` Playwright captures use valid pre-rendered JPEG fixtures with RGB validation.
  - **API Layer**: `fastapi.testclient.TestClient` tests all routes (`/api/domains`, `/api/run/*`, `/api/stop/*`, `/api/reports`, `/api/settings`, `/api/stats`).
- **Automatic Post-Test Cleanup & Logging (`pytest_sessionfinish`)**:
  - Automatically purges all test-generated output files, workbooks, reports, and screenshots from the filesystem.
  - Automatically drops any temporary test databases (`gamblingsites_test`).
  - Appends a clean run summary entry to [`test_run.log`](file:///f:/projects/gamblingwebfind/test_run.log).

---

## 3. Test File Layout

```
tests/
├── conftest.py                       # Global fixtures: mock_mongo, isolated_env, sample data
├── fixtures/                         # Realistic offline test data
│   ├── sample_domains.json           # Sample domain records
│   ├── sample_keywords.json          # Keyword lists
│   ├── sample_html_gambling.html     # High-density gambling HTML body
│   ├── sample_html_regular.html      # Hotel/resort regular HTML body
│   ├── sample_screenshot.jpg         # Valid 72KB RGB JPEG satisfying is_valid_screenshot
│   └── sample_screenshot_corrupt.jpg # 25-byte invalid image fixture
├── test_stage0_keywordssearch.py     # Stage 0: Search scraping, blocklist, Mongo bulk writes (11 tests)
├── test_stage1_domain_fetch.py       # Stage 1: Domain normalization, CSV/Excel/CommonCrawl seeding (7 tests)
├── test_stage2_checking_url.py       # Stage 2: Classifier, failure sniffer, timeout EMA, AI challenge (13 tests)
├── test_stage3_export_domains.py      # Stage 3: Screenshot validator, Excel exporter, Word/PDF reports (7 tests)
├── test_helpers_and_import.py        # True positive ingestion, batch splitter, compare tools (5 tests)
├── test_api.py                       # FastAPI routes, log streams, reports, settings, stats (8 tests)
├── test_end_to_end.py                # Full pipeline lifecycle: seed -> check -> export -> stats (1 test)
└── README.md                         # Test suite documentation (this file)
```

---

## 4. Test Coverage Summary

- **Total Test Cases**: 55 tests
- **Success Rate**: 100% (55 passed)
- **Execution Time**: ~3-8 seconds
- **External Dependencies**: Zero (pure pytest + mongomock)
