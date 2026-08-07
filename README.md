# 🎰 Gambling Website Hunter

**Automated Detection & Intelligence Gathering Tool for Gambling/Betting Websites**

An end-to-end automation pipeline that discovers, enumerates, verifies, and gathers intelligence on illegal online gambling and betting websites — saving everything into a structured CSV report with homepage screenshots.

---

## 🚀 What It Does

```
Google Dorking → DNS Resolution → Reverse IP Lookup → Liveness Check → Intel Gathering → Screenshots → CSV Report
```

| Phase | Description |
|---|---|
| **🔍 Dorking** | Searches Google with 130+ gambling-specific dork queries to auto-discover websites |
| **🌐 DNS + Reverse IP** | Resolves domains ↔ IPs, finds all domains sharing the same server |
| **✅ Liveness** | Checks if each domain is UP or DOWN (concurrent, fast) |
| **📊 Intel** | Gathers WHOIS, SSL certs, HTTP headers, page content, IP geolocation, tech stack |
| **📸 Screenshots** | Captures homepage screenshots using headless Chromium |
| **💾 CSV Export** | Consolidates all data into a 26-column CSV report |

---

## 📋 Features

- **Auto-Discovery** — 130+ built-in Google dork queries covering casinos, betting, rummy, teen patti, poker, slots, crypto gambling, color prediction, fantasy sports, lottery, satta/matka, and more
- **Reverse IP Enumeration** — Finds hidden domains hosted on the same server (HackerTarget + RapidDNS)
- **Multi-Source Input** — Start from Google dorking, domain lists, IP lists, or forensic PDF reports
- **Rich Intelligence** — WHOIS registration, SSL certificates, server technology, page titles, meta tags, IP geolocation
- **Screenshot Capture** — Playwright-based headless Chrome renders JS-heavy gambling sites correctly
- **Rate-Limited & Safe** — Configurable delays, random User-Agents, retry logic to avoid blocks
- **Concurrent Processing** — Multi-threaded liveness checking (10+ threads)
- **CSV Output** — 26-column report ready for Excel/Sheets analysis

---

## ⚡ Quick Start

### 1. Install

```bash
# Clone the repo
git clone https://github.com/nhiteshbohra/gamblingwebfind.git
cd gamblingwebfind

# One-command setup (installs all dependencies + Chromium browser)
python setup.py
```

### 2. Run

```bash
# Full auto — dork + discover + enumerate + check + intel + screenshot + CSV
python gambling_hunter.py --auto

# From an IP list
python gambling_hunter.py --ips iplist.txt

# From a domain list
python gambling_hunter.py --domains domains.txt

# From a forensic PDF
python gambling_hunter.py --pdf report.pdf

# Custom dork query
python gambling_hunter.py --dork "intitle:'cricket betting' site:.in"
```

> **Windows Users:** Prefix with `$env:PYTHONIOENCODING='utf-8';` for proper emoji display.

---

## 📖 Usage Examples

```bash
# Full auto mode with all default dorks
python gambling_hunter.py --auto

# Combine multiple sources
python gambling_hunter.py --auto --ips iplist.txt --domains extra_domains.txt

# Custom dork file
python gambling_hunter.py --dork-file my_custom_dorks.txt

# Control scan scope
python gambling_hunter.py --auto --max-domains 200 --max-reverse-ips 50

# Skip screenshots (faster)
python gambling_hunter.py --ips iplist.txt --no-screenshot

# Skip reverse IP lookup
python gambling_hunter.py --domains domains.txt --skip-reverse-ip

# Increase threads for faster liveness checks
python gambling_hunter.py --auto --threads 20

# Adjust rate limiting
python gambling_hunter.py --auto --delay 5
```

---

## 📁 Output

After running, all results are saved to `output/`:

```
output/
├── gambling_report_YYYYMMDD_HHMMSS.csv   # Main report (26 columns)
├── discovered_domains.txt                 # Domains found by dorking
├── live_domains.txt                       # Confirmed alive domains
├── screenshots/                           # Homepage screenshots
│   ├── example-casino.com.png
│   ├── bet-site.net.png
│   └── ...
└── scan_log.txt                           # Detailed execution log
```

### CSV Columns

| Column | Description |
|---|---|
| `domain` | Target domain name |
| `ip` | Resolved IPv4 address |
| `reverse_ip_domain_count` | Number of domains on same IP |
| `reverse_ip_domains` | Other domains on same IP (up to 20) |
| `status` | UP / DOWN / REDIRECT |
| `http_code` | HTTP status code |
| `response_time_ms` | Response time in milliseconds |
| `final_url` | Final URL after redirects |
| `page_title` | HTML page title |
| `meta_description` | Meta description tag |
| `meta_keywords` | Meta keywords tag |
| `content_language` | HTML lang attribute |
| `server` | Server header value |
| `technologies` | Detected technologies |
| `ssl_issuer` | SSL certificate issuer |
| `ssl_expiry` | SSL certificate expiry date |
| `whois_registrar` | Domain registrar |
| `whois_created` | Domain creation date |
| `whois_expires` | Domain expiry date |
| `whois_country` | Registrant country |
| `ip_country` | IP geolocation country |
| `ip_city` | IP geolocation city |
| `ip_isp` | Internet Service Provider |
| `screenshot_path` | Path to saved screenshot |
| `discovery_source` | How the domain was found |
| `scan_timestamp` | When the scan was performed |

---

## 🏗️ Architecture

```
gambling_hunter.py          # Main orchestrator (entry point)
├── config.py               # Central configuration
├── utils.py                # Shared utilities
├── dorker.py               # Google dorking engine
├── dorks.txt               # 130+ dork query templates
├── liveness.py             # Domain up/down checker
├── intel.py                # Intelligence gathering
├── screenshotter.py        # Screenshot capture (Playwright)
├── domain_ip.py            # Domain → IP resolution
├── ip_domain.py            # IP → Domains (reverse lookup)
├── datafrompdf.py          # PDF report extraction
├── requirements.txt        # Python dependencies
└── setup.py                # One-click installer
```

### Pipeline Flow

```
┌──────────────┐    ┌───────────────┐    ┌──────────────┐
│  Dorking      │    │  Domain List  │    │  IP List     │
│  (130+ dorks) │    │  (text file)  │    │  (text file) │
└──────┬───────┘    └──────┬────────┘    └──────┬───────┘
       │                   │                    │
       └───────────┬───────┘                    │
                   ▼                            │
          ┌────────────────┐                    │
          │ DNS Resolution │◄───────────────────┘
          │ domain → IP    │
          └───────┬────────┘
                  ▼
          ┌────────────────┐
          │ Reverse IP     │
          │ IP → domains   │
          └───────┬────────┘
                  ▼
          ┌────────────────┐
          │ Liveness Check │
          │ UP / DOWN      │
          └───────┬────────┘
                  ▼
          ┌────────────────┐
          │ Intel Gather   │
          │ WHOIS/SSL/Geo  │
          └───────┬────────┘
                  ▼
          ┌────────────────┐
          │ Screenshot     │
          │ Headless Chrome│
          └───────┬────────┘
                  ▼
          ┌────────────────┐
          │ CSV Export     │
          │ 26-col report  │
          └────────────────┘
```

---

## 🔍 Dork Categories

The tool ships with **130+ Google dork queries** organized into categories:

| Category | Examples |
|---|---|
| Rummy | Cash Rummy, Indian Rummy, 13 Card Rummy |
| Teen Patti / 3 Patti | Teen Patti Gold, real cash |
| Poker | Texas Holdem, Omaha, Live Poker |
| Casino | Online Casino, Live Casino, Mobile Casino |
| Slots / Jackpot | Slot Machine, Progressive Jackpot |
| Table Games | Roulette, Blackjack, Baccarat, Dragon Tiger, Andar Bahar |
| Sports Betting | Cricket, IPL, Football, Tennis, Horse Racing |
| Fantasy Sports | Fantasy Cricket, Fantasy Football, Cash Contest |
| Lottery | Online Lottery, Lucky Draw, Scratch Card |
| Color Prediction / Crash | Aviator, WinGo, Plinko, Mines |
| Crypto Casino | Bitcoin Casino, USDT Casino, Crypto Betting |
| Satta / Matka | Satta King, Matka Result |
| Bonus / Promo | Welcome Bonus, No Deposit Bonus, Promo Code |
| APK Downloads | Betting APK, Casino App Download |

You can add your own queries to `dorks.txt` (one per line).

---

## 🛠️ Requirements

- Python 3.10+
- Internet connection

### Python Packages

```
requests>=2.31.0
beautifulsoup4>=4.12.0
python-whois>=0.9.4
googlesearch-python>=1.2.0
playwright>=1.40.0
```

Installed automatically by `python setup.py`.

---

## ⚙️ Configuration

All settings are in [`config.py`](config.py):

| Setting | Default | Description |
|---|---|---|
| `DORK_DELAY_MIN` | 10s | Min delay between Google dork queries |
| `DORK_DELAY_MAX` | 30s | Max delay between dork queries |
| `LIVENESS_THREADS` | 10 | Concurrent threads for liveness checks |
| `LIVENESS_TIMEOUT` | 10s | Timeout per domain |
| `DEFAULT_TIMEOUT` | 15s | HTTP request timeout |
| `SCREENSHOT_WIDTH` | 1920 | Screenshot viewport width |
| `SCREENSHOT_HEIGHT` | 1080 | Screenshot viewport height |
| `SCREENSHOT_TIMEOUT` | 30s | Max time to capture screenshot |
| `MAX_DORK_RESULTS_PER_QUERY` | 50 | Results per dork query |

---

## ⚠️ Disclaimer

This tool is intended for **cybersecurity research, law enforcement, and regulatory compliance** purposes only. It helps identify illegal gambling websites for takedown and investigation.

- Always comply with local laws and regulations
- Respect website terms of service
- Use responsibly and ethically
- The authors are not responsible for misuse of this tool

---

## 📄 License

This project is for research and educational purposes.

---

## 🤝 Contributing

1. Fork the repo
2. Add your dork queries to `dorks.txt`
3. Submit a Pull Request

---

**Made with ❤️ for fighting illegal online gambling**
