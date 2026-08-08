# Presence — Gambling Domain Discovery Crawler

Presence is an autonomous crawler that discovers, fetches, and classifies gambling websites using keyword-driven search through a local SearXNG instance. It runs continuously, deduplicated everything before storing, and exports a clean CSV of confirmed gambling domains on a timer.

---

## What it does

1. **Discovers** — queries SearXNG with seed keywords to find candidate URLs
2. **Fetches** — downloads each page with user-agent rotation and per-domain rate limiting
3. **Classifies** — scores pages with a weighted signal detector (strong / medium / structural gambling signals), including `<meta description>` content
4. **Stores** — writes all results to a local SQLite database; verified gambling domains land in `data/presence_gambling_sites.csv`
5. **Re-checks** — on a 72-hour timer, re-verifies all `verified` URLs for liveness
6. **Monitors** — watchdog pings SearXNG health every 60s, detects crawler stalls, restarts the scheduler on crash, and can restart the Docker container if SearXNG goes down

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| Docker + Docker Compose | any recent version |

---

## Quick Start

### Step 1 — Clone and install Python dependencies

```bash
git clone <repo-url>
cd gamblingwebfind

pip install -r requirements.txt
```

`requirements.txt` installs:
- `aiohttp` — async HTTP client
- `aiosqlite` — async SQLite
- `beautifulsoup4` — HTML parsing
- `tldextract` — public-suffix-aware domain extraction
- `pyyaml` — config loading

---

### Step 2 — Start SearXNG (search backend)

Presence needs a local SearXNG instance to search the web. Start it with:

```bash
docker compose up -d
```

This starts:
- **SearXNG** on `http://127.0.0.1:8080` (web-only engines, no image/video/map noise)
- **Redis** (required by SearXNG internally)

Verify SearXNG is ready:

```bash
curl "http://127.0.0.1:8080/search?q=test&format=json"
```

You should see a JSON response with a `results` array.

> **Note:** The `searxng/settings.yml` config is mounted read-only into the container. It enables Google, Bing, and DuckDuckGo for general web search only. No changes are needed.

---

### Step 3 — Run the crawler

```bash
python cli/main.py start
```

The crawler will:
- Initialize the database (`data/presence.db`) on first run
- Seed keywords from `config/keywords_seed.txt`
- Begin discovery → fetch → classify cycles immediately
- Export `data/presence_gambling_sites.csv` every 30 minutes

Press `Ctrl+C` to stop cleanly.

---

## CLI Commands

All commands are run from the project root:

```bash
python cli/main.py <command> [args]
```

| Command | What it does |
|---|---|
| `start` | Start the autonomous crawler daemon |
| `stats` | Print current keyword count, URL status breakdown, and last export time |
| `add-keyword <phrase>` | Add a new search keyword to the seed pool |
| `export-now` | Force an immediate CSV export without waiting for the 30-min timer |
| `dispute <url_id>` | Mark a URL as disputed (excludes it from exports) |
| `import-domains <file.txt>` | Bulk-import a plain text list of domains/URLs |

### Examples

```bash
# Check what the crawler has found so far
python cli/main.py stats

# Add a new keyword manually
python cli/main.py add-keyword "crypto casino no kyc"

# Force export right now
python cli/main.py export-now

# Dispute a false positive (URL ID from the database)
python cli/main.py dispute 42

# Import a list of domains from a text file
python cli/main.py import-domains my_domains.txt
```

### `import-domains` file format

One domain or URL per line. Lines starting with `#` and blank lines are ignored. Scheme is optional — bare domains like `poker.com` are accepted.

```text
# Casino operators
draftkings.com
fanduel.com
https://caesars.com/online-casino

# Already-known domains are automatically skipped
betmgm.com
```

Output:
```
[Presence] Imported: 2 new, 1 already known (skipped)
```

---

## Output Files

| File | Contents |
|---|---|
| `data/presence.db` | SQLite database — all URLs, keywords, domains, meta |
| `data/presence_gambling_sites.csv` | Verified gambling domains (status=`verified`) — refreshed every 30 min |

### CSV columns

| Column | Description |
|---|---|
| `url` | Normalized URL |
| `domain` | Registered domain (e.g. `bet365.com`) |
| `confidence_score` | Classification score (0.0–1.0) |
| `first_seen_at` | When the URL was first discovered |
| `verified_at` | When it was last confirmed as gambling |

---

## Configuration

All settings live in [`config/settings.yaml`](config/settings.yaml):

```yaml
searxng_base_url: "http://127.0.0.1:8080"
searxng_page_delay_seconds: 1.0        # delay between SearXNG result pages
max_pages_per_keyword: 4               # max result pages fetched per keyword
fetch_timeout_seconds: 10              # HTTP request timeout
per_domain_delay_seconds: 2.0          # minimum gap between fetches to same domain
classification_threshold: 0.35         # minimum score to mark a page as gambling
max_new_keywords_per_page: 5           # keywords extracted from each verified page
liveness_recheck_interval_hours: 72    # how often verified URLs are re-checked
csv_export_interval_minutes: 30        # how often the CSV is refreshed
max_concurrent_fetches: 20             # parallel fetch workers
stall_alert_minutes: 30                # watchdog alert if no new URLs in this window
db_path: "data/presence.db"
csv_output_path: "data/presence_gambling_sites.csv"
```

---

## Keyword Seeds

Keywords are stored in `config/keywords_seed.txt` — one per line, `#` for comments:

```text
# Casino / slots
online casino real money
best online slots

# Sports betting
online sportsbook sign up
football betting sites

# Crypto / offshore
bitcoin casino no verification
```

The crawler rotates through all keywords, prioritizing those used least recently. Verified gambling pages automatically generate new extracted keywords from their content.

---

## Project Structure

```
gamblingwebfind/
├── cli/
│   └── main.py               # all CLI commands (start, stats, import-domains, ...)
├── config/
│   ├── keywords_seed.txt      # seed keyword list
│   ├── settings.yaml          # all tunable parameters
│   └── stopwords.txt          # words excluded from keyword extraction
├── core/
│   ├── classifier.py          # weighted gambling signal scorer
│   ├── dedup.py               # URL normalization and duplicate detection
│   ├── discovery.py           # SearXNG search integration
│   ├── fetcher.py             # async HTTP with rate limiting and retry
│   ├── keyword_extractor.py   # mines new keywords from verified pages
│   └── liveness.py            # re-checks verified URLs for liveness
├── orchestrator/
│   ├── scheduler.py           # main discovery + worker loop + liveness timer
│   ├── watchdog.py            # health monitor, stall detection, crash recovery
│   └── worker.py              # per-URL fetch → classify → store pipeline
├── storage/
│   ├── csv_exporter.py        # atomic CSV snapshot of verified URLs
│   ├── db.py                  # async SQLite CRUD layer
│   └── models.py              # CREATE TABLE SQL for all 4 tables
├── searxng/
│   └── settings.yml           # SearXNG engine config (web-only)
├── docker-compose.yml         # SearXNG + Redis
├── requirements.txt
└── presence-build-spec.md     # full technical specification
```

---

## Database Schema

**`keywords`** — search terms the crawler uses
```
id, term (UNIQUE), source (seed|extracted), source_url,
added_at, last_used_at, times_used
```

**`urls`** — every URL ever seen
```
id, url (UNIQUE), domain, discovered_via_keyword_id,
status (pending|verified|rejected|dead|disputed|removed),
confidence_score, classification_reasons,
first_seen_at, last_checked_at, verified_at
```

**`domains`** — per-domain rate limiting and stats
```
domain (PK), last_fetched_at, fetch_count, confirmed_count
```

**`meta`** — key/value store (currently stores `last_export_at`)
```
key (PK), value
```

---

## Classification Logic

Pages are scored by matching signals in visible text **and** `<meta name="description">` content:

| Tier | Weight | Examples |
|---|---|---|
| Strong | +0.25 | `deposit bonus`, `free spins`, `place a bet`, `live dealer`, `ukgc`, `mga/` |
| Medium | +0.10 | `casino`, `slots`, `poker`, `sportsbook`, `jackpot`, `betting` |
| Structural | +0.15 | `18+`, `responsible gambling`, `gambleaware`, `age verification` |

Score is capped at 1.0. Default threshold to classify as gambling: **0.35** (configurable).

---

## Watchdog Behavior

The watchdog runs every 60 seconds and:

1. **Stall detection** — if no new `pending` URL has appeared in the last `stall_alert_minutes`, logs an alert
2. **SearXNG health** — pings `http://127.0.0.1:8080`; if unreachable, runs `docker compose restart searxng`
3. **Scheduler crash recovery** — if the scheduler loop crashes, logs the exception and automatically restarts it

---

## Stopping and Restarting

```bash
# Stop the crawler
Ctrl+C

# Stop SearXNG
docker compose down

# Restart everything fresh
docker compose up -d
python cli/main.py start
```

The database (`data/presence.db`) persists across restarts. All previously discovered and verified URLs are preserved.

---

## Troubleshooting

**SearXNG returns no results**
- Check it's running: `docker compose ps`
- Check the health endpoint: `curl http://127.0.0.1:8080/search?q=test&format=json`
- Restart if needed: `docker compose restart searxng`

**Crawler discovers URLs but none get verified**
- Lower the classification threshold in `config/settings.yaml`: `classification_threshold: 0.20`
- Check `presence stats` — if all URLs land as `rejected`, the sites found aren't scoring high enough

**`ModuleNotFoundError`**
- Run all commands from the project root (`gamblingwebfind/`)
- Make sure `pip install -r requirements.txt` completed without errors

**Database locked errors**
- Only one `python cli/main.py start` process should run at a time
- Kill any existing processes before restarting
