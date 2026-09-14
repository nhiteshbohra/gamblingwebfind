# gamblingwebfind — Automated Online Gambling Discovery & Compliance Pipeline

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.0%2B-green.svg)](https://www.mongodb.com/)
[![OmniRoute](https://img.shields.io/badge/OmniRoute-AI_Gateway-6366f1.svg)](https://github.com/diegosouzapw/OmniRoute)
[![Ollama](https://img.shields.io/badge/Ollama-Local_AI-black.svg)](https://ollama.ai/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-red.svg)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, high-performance intelligence pipeline for discovering, classifying, validating, and generating audit-ready compliance reports for online gambling and illegal wagering websites operating in India and globally.

---

## 🏛️ Repository Architecture & Branches

To provide maximum flexibility between **cloud speed** and **local privacy**, the codebase is organized into **two specialized production branches**:

```
                                  MAIN BRANCH (Hub & Documentation)
                                                 │
                   ┌─────────────────────────────┴─────────────────────────────┐
                   │                                                           │
                   ▼                                                           ▼
    BRANCH: gamblingwebfind-omniroute                       BRANCH: gamblingwebfind-core
         (Cloud AI Gateway)                                      (100% Local AI)
  • 1.5 Billion tokens/month                              • 100% offline & air-gapped
  • Powered by OmniRoute Gateway                          • Powered by Ollama
  • 490+ Cloud Models (Llama 3.3 70B, Qwen 72B)           • Local GPU Models (Qwen 3B/7B, Llama 8B)
  • 150–400 tokens/second                                 • 20–40 tokens/second
  • Auto-healing background daemon                        • Zero external network calls
  • Best for: 740,000+ domain bulk processing             • Best for: Restricted environments / Air-gap
```

| Branch Name | Primary AI Engine | Target Use Case | Recommended Command |
| :--- | :--- | :--- | :--- |
| **[`gamblingwebfind-omniroute`](#branch-1-omniroute-edition)** | **OmniRoute Cloud AI Gateway** | **Production & Bulk Scanning** (740,000+ domains, fast, low local hardware) | `git checkout gamblingwebfind-omniroute` |
| **[`gamblingwebfind-core`](#branch-2-ollama-edition)** | **Ollama Local LLMs** | **Offline & Air-Gapped Scanning** (Zero API keys, private local inference) | `git checkout gamblingwebfind-core` |

*(Note: `gamblingwebfind-ollama` is also available as an alias for `gamblingwebfind-core`)*

---

## ⚖️ Head-to-Head Comparison: OmniRoute vs. Ollama

| Evaluation Criteria | ⚡ Branch 1: `gamblingwebfind-omniroute` | 🦙 Branch 2: `gamblingwebfind-core` (Ollama) |
| :--- | :--- | :--- |
| **Inference Engine** | Cloud AI Gateway via **OmniRoute** | Local LLM daemon via **Ollama** |
| **Processing Speed** | **150 – 400+ tokens/sec** | **20 – 40 tokens/sec** (dependent on GPU) |
| **Throughput (740k domains)**| **~1.5 to 2 days total** (concurrency 15–20) | **~12 to 15 days 24/7** (concurrency 2–3) |
| **Monthly Cost / Quota** | **1.5 Billion tokens/month** (Free via API key) | **$0 / Free** (electricity & hardware only) |
| **Tokens Required for 740k** | **~180M – 350M tokens** (*uses <25% of quota*) | N/A (runs on local hardware) |
| **Available Models** | **70B+ Foundation Models** (Llama 3.3 70B, Qwen 2.5 72B, DeepSeek V3) | **3B – 8B Quantized Models** (`qwen2.5:3b`, `qwen2.5:7b-instruct`, `llama3:8b`) |
| **Local Hardware Load** | **Near 0% GPU / CPU load**. PC runs cool and quiet | **100% GPU VRAM saturation**, fan noise, high temps |
| **System Requirements** | Any basic PC / Laptop (4GB RAM, no GPU required) | Dedicated NVIDIA GPU (minimum 6GB–8GB+ VRAM) |
| **Indian Slang & Nuances** | **Superior precision** on Satta Matka, Khai-Lagai, Mahadev Book funnels | Moderate; 3B/7B models can miss edge cases |
| **JSON Schema Adherence** | **99.9%** (No markdown hallucination, valid JSON) | ~92–95% (Requires parsing fallbacks & retries) |
| **Vision (Screenshots)** | Cloud vision (Llama 3.2 Vision / Qwen-VL) <1 sec | Local vision (LLaVA/MiniCPM) 4–8 sec (heavy VRAM) |
| **Network Requirements** | Requires internet access to reach OmniRoute | **100% offline & air-gapped capable** |
| **Self-Healing Capabilities**| **Built-in Auto-Start** (`start_omniroute_if_needed`) | Requires local Ollama service to stay active |

---

## 🎯 Which Branch Should You Use?

### Choose **`gamblingwebfind-omniroute`** (Recommended ⭐) if:
- You need to process large domain lists (**50,000 to 740,000+ domains**) in days rather than weeks.
- You want higher accuracy from **70B+ models** without false positives on news sites, schools, or hotels.
- You are running on a standard laptop or desktop without a high-end NVIDIA graphics card.
- You have an OmniRoute API key (with your 1.5B token allocation).

### Choose **`gamblingwebfind-core`** if:
- You must operate in an air-gapped, offline, or highly classified environment.
- You are strictly prohibited from transmitting website excerpts or domain names over the internet.
- You have a dedicated machine with an NVIDIA GPU (RTX 3060/4060 12GB+) and want a 100% self-hosted setup.

---

## 🚀 Step-by-Step Installation & Usage Guide

---

### Branch 1: OmniRoute Edition (`gamblingwebfind-omniroute`)

#### 1. Switch to Branch
```bash
git checkout gamblingwebfind-omniroute
```

#### 2. Install Prerequisites
- **Python**: 3.11 or 3.12 (`python --version`)
- **Node.js & NPM**: Node 18+ (`node --version`)
- **MongoDB**: Local Community Server (port 27017) or MongoDB Atlas connection string.

#### 3. Install OmniRoute Gateway
```bash
npm install -g omniroute
```
*(Verify with `omniroute --help`)*

#### 4. Setup Python Virtual Environment
```bash
# Create and activate venv
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

#### 5. Install Playwright Chromium Browser
```bash
python -m playwright install chromium
```

#### 6. Configure Environment Variables
Create `.env` from `.env.example`:
```bash
cp .env.example .env
```
Fill in your configuration:
```env
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gambling_detector

# OmniRoute Gateway Settings
OMNIROUTE_BASE_URL=http://localhost:20128/v1
OMNIROUTE_API_KEY=your_omniroute_api_key_here
OMNIROUTE_MODEL=auto
OMNIROUTE_VALIDATOR_MODEL=auto
OMNIROUTE_TIEBREAKER_MODEL=auto

# Performance Settings
CHECK_CONCURRENCY=20
AI_CONCURRENCY=12
AI_FAST_MODE=false
SCREENSHOT_DIR=data/screenshots
```

#### 7. Run the Pipeline
```bash
# A. Interactive Menu (All tools in one):
python main.py

# B. Direct High-Speed Classifier:
python -m checking_url.runner --mode new --concurrency 20

# C. Seed a CSV/Excel file of domains and run immediately:
python -m checking_url.runner --mode new --file path/to/domains.csv

# D. Launch Web Dashboard:
uvicorn api.main:app --port 8000
```
*(Access web dashboard at `http://localhost:8000`)*

#### 8. Run Automated Test Suite
```bash
pytest -v tests
```
*(23/23 unit and integration tests passing)*

---

### Branch 2: Ollama Edition (`gamblingwebfind-core`)

#### 1. Switch to Branch
```bash
git checkout gamblingwebfind-core
```
*(or `git checkout gamblingwebfind-ollama`)*

#### 2. Install Prerequisites
- **Python**: 3.11 or 3.12
- **MongoDB**: Local Community Server (port 27017)
- **Ollama**: Download and install from [ollama.com](https://ollama.com/download)

#### 3. Pull Required Ollama Models
Open your terminal and pull the models:
```bash
# Lightweight fast model (Recommended for 6GB-8GB GPUs):
ollama pull qwen2.5:3b

# High reasoning model (Recommended for 12GB+ GPUs):
ollama pull qwen2.5:7b-instruct
```

#### 4. Setup Python Virtual Environment
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1   # or source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### 5. Install Playwright Chromium Browser
```bash
python -m playwright install chromium
```

#### 6. Configure Environment Variables
Create `.env`:
```bash
cp .env.example .env
```
Configure for local Ollama:
```env
MONGO_URI=mongodb://localhost:27017/
MONGO_DB_NAME=gambling_detector

# Local Ollama Settings
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_VALIDATOR_MODEL=qwen2.5:3b
OLLAMA_TIEBREAKER_MODEL=qwen2.5:7b-instruct

# Set concurrency based on your GPU VRAM:
CHECK_CONCURRENCY=10
AI_CONCURRENCY=3
AI_FAST_MODE=true
SCREENSHOT_DIR=data/screenshots
```

#### 7. Run the Pipeline
```bash
# A. Interactive Menu:
python main.py

# B. Direct Runner:
python -m checking_url.runner --mode new --concurrency 10

# C. Web Dashboard:
uvicorn api.main:app --port 8000
```

---

## 🛠️ Common Workflows Across Both Branches

### 1. Keyword Harvesting (Stage 0)
Search engines (Bing, DuckDuckGo, Yahoo, Google) are automatically scraped for fresh gambling targets:
```bash
python main.py
# Select Option 1: "Run Keyword Harvest"
```

### 2. Exporting Compliance Reports (Stage 3)
Generate official audit dossiers with clickable hyperlinks and embedded evidence:
```bash
python main.py
# Select Option 3: "Generate PDF / Excel Export"
```
- Bundles are saved in `output/` as `.pdf` and `.xlsx`.
- Files automatically split at **24MB** or **1,000 links** to adhere to regulatory upload limits.

### 3. Re-Checking Blocked Domains
Monitor whether previously reported sites have been taken down by ISPs or authorities:
```bash
python -m checking_url.runner --mode blocked
```

### 4. Training the Machine Learning Fast-Path Model
Update the sub-millisecond domain classifier (`data/domain_ml_model.joblib`) using your latest confirmed database records:
```bash
python ml_trainer.py
```

---

## 📂 Repository Quick-Reference

```
.
├── main branch                       # Master documentation & branch navigation hub
│   └── README.md                     # You are here
│
├── gamblingwebfind-omniroute branch  # Cloud AI Gateway (Production)
│   ├── api/                          # FastAPI REST application
│   ├── checking_url/                 # Triple-lock verifier & OmniRoute engine
│   ├── data/                         # Keywords, ML model, screenshot archives
│   ├── db/                           # MongoDB client & queries
│   ├── export_domains/               # PyMuPDF PDF/Excel batch exporter
│   ├── keywordssearch/               # Crawlee search engine scraper
│   ├── tests/                        # Full Pytest test suite (23 passing)
│   ├── web/                          # Live web dashboard interface
│   ├── ARCHITECTURE.md               # Cloud technical architecture specs
│   └── README.md                     # OmniRoute branch guide
│
└── gamblingwebfind-core branch       # Local AI Engine (Air-Gapped)
    ├── api/                          # FastAPI REST application
    ├── checking_url/                 # Triple-lock verifier & Ollama engine
    ├── data/                         # Keywords, ML model, screenshot archives
    ├── db/                           # MongoDB client & queries
    ├── export_domains/               # PyMuPDF PDF/Excel batch exporter
    ├── keywordssearch/               # Crawlee search engine scraper
    ├── web/                          # Live web dashboard interface
    ├── ARCHITECTURE.md               # Local technical architecture specs
    └── README.md                     # Ollama branch guide
```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` in the respective branches for details.
