# List Fetcher (`list_fetcher`)

Automated high-performance parser and bulk importer for curated gambling and illegal site blocklists into MongoDB queue (**`domain_Listed` / `MONGO_COLLECTION`**).

---

## 🔄 End-to-End Pipeline Flow

Blocklist domains are **NOT** pre-written to `checked_domains`. Instead, they enter as fresh, unprocessed queue items in `domain_Listed`, flowing through the complete verification pipeline:

```text
┌──────────────────────────────────────────────────────────┐
│  Stage 0/1: List Fetcher (`list_fetcher`)                 │
│  Parses curated external blocklists from sources.txt      │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│  MongoDB: `domain_Listed` (`MONGO_COLLECTION`)           │
│  { _id, domain, active: true, processed: false, source } │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│  Stage 2: `checking_url` Runner                          │
│  • Fast fetch & TLS impersonation                        │
│  • Layer 2: 988 gambling keywords + heuristic screening  │
│  • Layer 3: Ollama AI 2-Round Challenge                  │
│  • Immediate Playwright screenshot for gambling sites    │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│  MongoDB: `checked_domains` & sync `domain_Listed`       │
│  • Full verdict stored in `checked_domains`              │
│  • `processed: true` marked in `domain_Listed`           │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│  Stage 3/4: `export_domains`                             │
│  Batched Word (.docx), Excel (.xlsx), and PDF reports    │
└──────────────────────────────────────────────────────────┘
```

---

## 🛡️ Multi-Tier Strict Deduplication

The pipeline ensures **zero duplicate domains** through 3 layers:

1. **Intra-Source Deduplication**: Deduplicates domains within each file/URL.
2. **Cross-Source Global Deduplication**: If a domain appears in multiple lists (e.g. Estonia + AdGuard + Hagezi), it is processed only once across the entire run.
3. **Database Pre-Check**: Checks MongoDB before inserting — existing domains in `domain_Listed` are automatically skipped to avoid duplicate queue items. (Use `--overwrite` to re-queue).

---

## 📌 Configured Sources

### 1. Curated & Official Registries
- **Estonia Blocked Gambling Websites**: `https://github.com/elliotwutingfeng/Estonia-Blocked-Gambling-Websites/blob/main/blocklist.txt`
- **TEQSA Illegal Cheating Websites**: `https://github.com/elliotwutingfeng/TEQSA-illegal-cheating-websites/blob/main/urls.txt`
- **ACMA Blocked Gambling Websites**: `https://github.com/elliotwutingfeng/ACMA-blocked-gambling-websites/blob/main/urls.txt`
- **Arkynx Gambling Blocklist**: `https://github.com/arkynx/blocklists/blob/main/gambling-domains.txt`
- **AdGuardHome Gambling Filter**: `https://github.com/alexsannikov/adguardhome-filters/blob/master/gambling.txt`
- **ph00lt0 Domain Blocklist**: `https://github.com/ph00lt0/blocklist/blob/master/domains.txt`
- **Eimji Gambling Hosts**: `https://github.com/Eimji/hosts/blob/master/gambling_hosts.txt`
- **BlocklistProject Gambling**: `https://raw.githubusercontent.com/blocklistproject/Lists/refs/heads/main/gambling.txt`

### 2. Hagezi DNS Gambling Blocklists (`hagezi/dns-blocklists`)
- **Hagezi Only-Domains (Full)**: `wildcard/gambling-onlydomains.txt`
- **Hagezi Only-Domains (Medium)**: `wildcard/gambling.medium-onlydomains.txt`
- **Hagezi Only-Domains (Mini)**: `wildcard/gambling.mini-onlydomains.txt`
- **Hagezi Domains Format**: `domains/gambling.txt`
- **Hagezi RPZ Format**: `rpz/gambling.txt`
- **Hagezi AdBlock Format**: `adblock/gambling.txt`
- **Hagezi Dnsmasq Format**: `dnsmasq/gambling.txt`
- **Hagezi Hosts Format**: `hosts/gambling.txt`

---

## 🚀 How to Run

### 1. Fetch & Queue All Sources into `domain_Listed`
```bash
python list_fetcher/keysfetch_from_txt.py
```
Or via main interactive menu:
```bash
python main.py
# Select Option 6: list_fetcher
```

### 2. Dry Run Preview (Count fresh vs duplicate domains without writing)
```bash
python list_fetcher/keysfetch_from_txt.py --dry-run
```

### 3. Import Specific Sources Only
```bash
# Import Estonia and ACMA lists:
python list_fetcher/keysfetch_from_txt.py --sources estonia_gambling acma_gambling

# Import Hagezi lists:
python list_fetcher/keysfetch_from_txt.py --sources hagezi_gambling_onlydomains hagezi_adblock_gambling
```

### 4. Import from a Custom URL or Local Text File
```bash
# Custom GitHub URL (both web /blob/ or raw.githubusercontent.com work)
python list_fetcher/keysfetch_from_txt.py --custom-url https://github.com/user/repo/blob/main/blocklist.txt

# Custom local text / CSV / Excel file
python list_fetcher/keysfetch_from_txt.py --custom-file path/to/my_domains.txt
```

### 5. Check Current Database Stats
```bash
python list_fetcher/keysfetch_from_txt.py --stats
```

### 6. Process the Queued Domains
After importing, run Stage 2 checking to classify and capture screenshots:
```bash
python main.py
# Select Option 2: checking_url -> Option 1: Check New Domains
# Or directly:
python -m checking_url.runner --mode new
```

---

## 🗄️ MongoDB Document Format

Each domain is queued strictly into **`domain_Listed`**:
```json
{
  "_id": "example-casino.com",
  "domain": "example-casino.com",
  "active": true,
  "processed": false,
  "source": "blocklist:estonia_gambling",
  "added_date": "2026-09-08"
}
```
When `checking_url` processes the domain, it evaluates the site and records the verdict into **`checked_domains`** while updating `processed: true` in **`domain_Listed`**.
