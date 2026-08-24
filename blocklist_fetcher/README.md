# Blocklist Fetcher (`blocklist_fetcher`)

Automated high-performance parser and bulk importer for curated gambling and illegal site blocklists into MongoDB (`checked_domains` & `domain_Listed`).

---

## 🛡️ Multi-Tier Strict Deduplication

The pipeline ensures **zero duplicate domains** through 3 layers:

1. **Intra-Source Deduplication**: Deduplicates domains within each file/URL.
2. **Cross-Source Global Deduplication**: If a domain appears in multiple lists (e.g. Estonia + AdGuard + Hagezi), it is processed only once across the entire run.
3. **Database Pre-Check**: Checks MongoDB before inserting — existing domains in `checked_domains` are automatically skipped to preserve already-classified data and avoid duplicate writes. (Use `--overwrite` to force updates).

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

### 1. Fetch & Import All Sources into MongoDB (Zero Duplicates)
```bash
python blocklist_fetcher/keysfetch_from_txt.py
```

### 2. Dry Run Preview (Count fresh vs duplicate domains without writing)
```bash
python blocklist_fetcher/keysfetch_from_txt.py --dry-run
```

### 3. Import Specific Sources Only
```bash
# Import Estonia and ACMA lists:
python blocklist_fetcher/keysfetch_from_txt.py --sources estonia_gambling acma_gambling

# Import Hagezi lists:
python blocklist_fetcher/keysfetch_from_txt.py --sources hagezi_gambling_onlydomains hagezi_adblock_gambling
```

### 4. Import from a Custom URL or Local Text File
```bash
# Custom GitHub URL (both web /blob/ or raw.githubusercontent.com work)
python blocklist_fetcher/keysfetch_from_txt.py --custom-url https://github.com/user/repo/blob/main/blocklist.txt

# Custom local text / CSV / Excel file
python blocklist_fetcher/keysfetch_from_txt.py --custom-file path/to/my_domains.txt
```

### 5. Check Current Database Stats
```bash
python blocklist_fetcher/keysfetch_from_txt.py --stats
```

---

## 🗄️ MongoDB Document Format

Each domain is upserted with `_id = domain` into **`checked_domains`**:
```json
{
  "_id": "example-casino.com",
  "domain": "example-casino.com",
  "url": "https://example-casino.com",
  "status": "gambling",
  "reason": "Known blocklist: <Source Name>",
  "source": "blocklist:<source_id>",
  "decided_by": "external_blocklist",
  "screenshot_taken": false,
  "added_date": "2026-08-24"
}
```
And synced to **`domain_Listed`**:
```json
{
  "_id": "example-casino.com",
  "domain": "example-casino.com",
  "active": true,
  "processed": true,
  "source": "blocklist:<source_id>",
  "added_date": "2026-08-24"
}
```
