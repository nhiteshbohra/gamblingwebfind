# gamblingwebfind — Ollama Edition (100% Local AI Discovery Pipeline)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.0%2B-green.svg)](https://www.mongodb.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Local_AI-black.svg)](https://ollama.ai/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-red.svg)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, privacy-first Python pipeline for discovering, classifying, validating, and generating audit-ready compliance reports for online gambling and illegal wagering websites across India and globally.

This branch (`gamblingwebfind-core` / `gamblingwebfind-ollama`) runs **100% locally on your own hardware** using **Ollama**, requiring **zero cloud API keys, zero token fees, and zero external network transmission of page content**.

---

## 1. Key Capabilities

- **100% Local & Air-Gapped AI Inference**:
  - Powered by local open-weight models running via **Ollama** (`http://localhost:11434`).
  - Recommended models: `qwen2.5:3b` or `qwen2.5:7b-instruct` or `llama3:8b`.
  - Zero subscription fees, zero API key dependencies, and complete data privacy.
- **Multi-Source Domain Discovery**:
  - Automated Crawlee search harvesting (Google, Bing, DuckDuckGo) via `keywordssearch/crawlee_search.py`.
  - Bulk seed file ingestion from text, CSV, or Excel spreadsheets directly into MongoDB.
- **TLS-Impersonating HTTP Engine**:
  - Uses `Scrapling` with `curl_cffi` Chrome fingerprinting to bypass WAFs and anti-bot blocks without triggering CAPTCHAs.
- **Triple-Lock Classification Engine**:
  1. *ML Fast-Path*: Pre-trained scikit-learn model (`checking_url/ml_classifier.py`) evaluates domain linguistic features in <0.1ms.
  2. *Weighted Heuristic Pre-Screen*: 1,000+ weighted gambling terms, TLD anchors (`.casino`, `.bet`, `.poker`), and Indian regional wagering keywords (*Satta Matka, Khai-Lagai, Mahadev Book, Teen Patti*).
  3. *Dual-Round Local AI Challenge*:
     - **Round 1**: Evaluates extracted DOM text and CTAs.
     - **Round 2 (Evidence Challenge)**: AI must quote verbatim evidence from the page proving it is non-gambling; if evidence is weak, verdict is overridden to gambling.
- **Evidentiary Visual Capture (Playwright)**:
  - Immediate headless browser capture using an asynchronous `BrowserPool`. High-resolution PNG screenshots are saved for all confirmed gambling portals.
- **Automated Compliance Reporting (`export_domains`)**:
  - Generates audit-ready PDF (`.pdf`) and Excel (`.xlsx`) report bundles with clickable hyperlinks, categorized evidence, and auto-split sizing bounds ($\le 24\text{ MB}$ or $1,000$ links) powered by PyMuPDF.
- **Web Dashboard & REST API (`api/`)**:
  - Full-stack FastAPI interface with live analytics, interactive domain inspector proxy, queue progress, and configuration editor.

---

## 2. Hardware Requirements

| Resource | Minimum | Recommended |
| :--- | :--- | :--- |
| **GPU** | NVIDIA GPU with 6GB VRAM (e.g. GTX 1660 / RTX 3050) | NVIDIA GPU with 8GB–12GB+ VRAM (e.g. RTX 3060, 4060, 4070) |
| **CPU** | 4 cores / 8 threads (if running on CPU) | 8+ modern cores |
| **RAM** | 16 GB DDR4 | 32 GB DDR4/DDR5 |
| **Storage** | 10 GB SSD free space (for Ollama models & screenshots) | 50+ GB NVMe SSD |

---

## 3. Prerequisites & Installation

### Step 1: Clone and Checkout the Ollama Branch

```bash
git clone https://github.com/your-username/gamblingwebfind.git
cd gamblingwebfind
git checkout gamblingwebfind-core
```
*(Or `git checkout gamblingwebfind-ollama`)*

---

### Step 2: Install & Start Ollama

1. Download and install Ollama from [ollama.com](https://ollama.com/download).
2. Pull the required classification models:

```bash
# Recommended lightweight model (fast inference, ~2GB VRAM):
ollama pull qwen2.5:3b

# Recommended balanced model (higher reasoning, ~5GB VRAM):
ollama pull qwen2.5:7b-instruct

# Alternative Llama 3 model:
ollama pull llama3:8b
```

3. Ensure the Ollama service is running:
```bash
curl http://localhost:11434/api/tags
```

---

### Step 3: Set Up Python Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 4: Install Playwright Chromium

```bash
python -m playwright install chromium
```

---

### Step 5: Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Configure your local Ollama settings in `.env`:

```env
# ── MongoDB Configuration ──
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gambling_detector

# ── Local Ollama Configuration ──
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_VALIDATOR_MODEL=qwen2.5:3b
OLLAMA_TIEBREAKER_MODEL=qwen2.5:7b-instruct

# ── Concurrency & Limits (Set conservatively for local GPU) ──
CHECK_CONCURRENCY=10
AI_CONCURRENCY=3
AI_FAST_MODE=true

# ── Screenshot Storage ──
SCREENSHOT_DIR=data/screenshots
```

> [!TIP]
> If your GPU has 8GB VRAM or less, keep `AI_CONCURRENCY=2` or `3` to avoid GPU Out-Of-Memory (OOM) errors.

---

## 4. How to Run

### Interactive CLI Menu

```bash
python main.py
```

### Direct Verification Runner

```bash
# Process new domains
python -m checking_url.runner --mode new --concurrency 10

# Test first 100 domains
python -m checking_url.runner --mode new --limit 100 --concurrency 10

# Ingest and scan a CSV file
python -m checking_url.runner --mode new --file path/to/domains.csv
```

### Live Web Dashboard

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
Navigate to `http://localhost:8000` to inspect live results.

---

## 5. License

Distributed under the MIT License. See `LICENSE` for more information.
