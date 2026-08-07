# 🎰 Gambling Site Discovery & Intelligence Platform

**Continuous Autonomous OSINT Pipeline for Regulatory, Investigative & Law Enforcement Use**

An automated, non-stop OSINT intelligence platform that discovers online gambling/betting operations, parses and normalizes root domains, scores and classifies site content, maps hosting infrastructure (DNS A/AAAA resolution & co-hosted reverse-IP discovery), gathers deep technical evidence (WHOIS, SSL, tech stack, redirect chains), and captures full-page homepage screenshots.

Supports **Continuous Daemon Mode** (`--daemon`) to run indefinitely without stopping, paginating across multi-page search results and directory aggregators.

---

## ⚙️ Pipeline Architecture

```
[1. Discovery (SearXNG/DuckDuckGo Multi-Page & Directory Scraper)]
                                      ↓
                  [2. Domain Normalization (tldextract)]
                                      ↓
              [3. Stage-1 Crawl & Content Classifier]
                                      ↓
           [4 & 5. DNS Resolution & Co-Hosted Reverse-IP]
                                      ↓
         [6 & 7. Deep Intelligence (WHOIS, SSL, Tech, Headers)]
                                      ↓
                   [8. Playwright Screenshot Capture]
                                      ↓
             [9. Dynamic Keyword & Dork Auto-Expansion]
                                      ↓
             [💾 Storage: 4 CSV Datasets + Screenshots]
```

---

## ⚡ Quick Start & Usage Commands

### 1. Continuous Daemon Mode (Runs Indefinitely)

```bash
# Continuous autonomous loop — press Ctrl+C to stop cleanly
python run_pipeline.py --daemon

# Daemon mode with custom cycle pause interval (e.g. 120 seconds between sweeps)
python run_pipeline.py --daemon --cycle-delay 120
```

### 2. Single Sweep Mode

```bash
# Run a single full sweep (Search discovery -> Classification -> Resolution -> Enrichment -> Screenshots)
python run_pipeline.py --all

# Deep search pagination (crawl up to 10 pages per query)
python run_pipeline.py --all --max-pages 10
```

### 3. Custom Input Domain Mode

```bash
# Run pipeline on a custom seed list of domain names
python run_pipeline.py --input-domains seed_domains.txt --all
```

---

## 📊 Output Datasets

1. **`data/discovered_domains.csv`**
   `domain, keyword, search_engine, rank, discovered_at, source_url`

2. **`data/classified_domains.csv`**
   `domain, is_gambling, confidence_score, title, classification_reason, checked_at`

3. **`data/ip_mapping.csv`**
   `domain, resolved_ip, asn, hosting_provider, co_hosted_domains, checked_at`

4. **`data/site_intelligence.csv`**
   `domain, ip, status_code, is_active, title, meta_description, meta_keywords, ssl_issuer, ssl_valid_from, ssl_valid_to, whois_registrar, whois_created_date, whois_expiry_date, technologies, http_headers, redirect_chain, screenshot_path, last_checked`

5. **`screenshots/`**
   Full-page PNG evidence screenshots saved as `<domain>.png`.
