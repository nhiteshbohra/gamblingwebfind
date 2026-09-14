# ARCHITECTURE — gamblingwebfind System & Data Specifications (Ollama Edition)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.0%2B-green.svg)](https://www.mongodb.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Local_AI-black.svg)](https://ollama.ai/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-red.svg)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

This document details the technical specifications, data models, and execution architecture of **gamblingwebfind** (Ollama edition).

---

## 1. High-Level Architecture

The platform runs 100% on local infrastructure, processing domains through a multi-stage funnel designed to maximize local GPU utilization while preventing out-of-memory crashes.

```
                      ┌──────────────────────────────────────────────┐
                      │                 USER INTERFACE               │
                      │   Interactive CLI (main.py) / FastAPI REST   │
                      └──────────────────────┬───────────────────────┘
                                             │
             ┌───────────────────────────────┴───────────────────────────────┐
             │                                                               │
             ▼                                                               ▼
┌─────────────────────────┐                                   ┌─────────────────────────┐
│ Stage 0: Search Harvest │                                   │ Bulk Domain Seed Files  │
│ Crawlee Multi-Engine    │                                   │ CSV / Excel / TXT       │
└────────────┬────────────┘                                   └────────────┬────────────┘
             │                                                             │
             └───────────────────────────┬─────────────────────────────────┘
                                         │
                                         ▼
                             ┌──────────────────────┐
                             │ MongoDB Data Store   │
                             │  • domain_Listed     │
                             │  • checked_domains   │
                             └───────────┬──────────┘
                                         │
                                         ▼
                             ┌──────────────────────┐
                             │ Stage 2: URL Checker │
                             │  • Scrapling Fetcher │
                             │  • 1,000 Heuristics  │
                             │  • ML Fast-Path      │
                             │  • Local Ollama AI   │
                             │  • Playwright Pool   │
                             └───────────┬──────────┘
                                         │
                                         ▼
                             ┌──────────────────────┐
                             │ Stage 3: Exporter    │
                             │  • PDF / Excel Report│
                             │  • 24MB Size Splitter│
                             └──────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 Stage 0: Discovery Engine (`keywordssearch/crawlee_search.py`)
- Executes automated queries against search engines (Bing, DuckDuckGo) for Indian and international gambling keywords.
- Extracted domain targets are de-duplicated and stored in MongoDB `domain_Listed`.

### 2.2 Stage 1: Data Ingestion (`db/mongo_client.py`)
- Normalizes URLs, stripping protocols, sub-paths, and `www.` prefixes.
- Tracks queue states (`active`, `processed`, `status`).

### 2.3 Stage 2: Verification Engine (`checking_url/`)
- **`fetcher.py`**: High-performance HTTP client using `Scrapling` and `curl_cffi` to mimic Chrome TLS signatures.
- **`ml_classifier.py`**: Scikit-learn domain name feature classifier running in <0.1ms.
- **`classifier.py`**: Evaluates 1,000+ weighted keyword signals and guardrail archetypes.
- **`ai_classifier.py` (Ollama Engine)**:
  - Communicates directly with local Ollama service (`http://localhost:11434/api/chat`).
  - **Round 1 (Analyst)**: Analyzes text excerpts, metadata, and payment funnels.
  - **Round 2 (Evidence Challenge)**: Challenges suspected regular verdicts to cite verbatim text proving non-gambling operations.
  - **Tie-Breaker**: Arbitrated by `qwen2.5:7b-instruct`.
- **`export_domains/screenshot.py`**: Playwright asynchronous Chromium browser pool for instant evidentiary screenshot capture.

### 2.4 Stage 3: Compliance Reporting (`export_domains/`)
- Generates evidence dossiers in PDF and Excel formats.
- Splits files automatically if exceeding 24MB or 1,000 links.

---

## 3. Local GPU Tuning & Concurrency

When running local LLMs via Ollama, concurrency must be carefully balanced against available VRAM:

| VRAM Available | Recommended Model | `AI_CONCURRENCY` | `CHECK_CONCURRENCY` |
| :--- | :--- | :--- | :--- |
| **6 GB** | `qwen2.5:3b` | 1 – 2 | 8 |
| **8 GB** | `qwen2.5:3b` / `llama3:8b` | 2 – 3 | 12 |
| **12 GB** | `qwen2.5:7b-instruct` | 3 – 4 | 15 |
| **16 GB+** | `qwen2.5:7b-instruct` | 4 – 6 | 20 |
