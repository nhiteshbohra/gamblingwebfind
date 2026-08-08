# Presence — complete project guide

Everything about the project in one place: what it does, how data moves through it, and exactly how each file works internally. Written for a builder (human or AI agent) who has zero prior context on this conversation.

---

## 1. What Presence does, in one paragraph

Presence is a background service that never stops running. It starts with a small list of gambling-related words. It uses those words to search the web through a locally-hosted search engine (SearXNG), collects every URL that search turns up, visits each one, and decides whether that page is really a gambling/betting/casino site. If yes, the URL gets saved permanently and the page's own text is mined for new words related to gambling — words that get added back into the search list. This is what makes it "continuous": every confirmed site makes the crawler smarter about what to search for next, so it keeps finding new sites without you ever adding a keyword by hand. Everything confirmed and still online gets written out to a CSV file that's the actual deliverable — a blocklist.

---

## 2. Full folder structure

```
presence/
├── docker-compose.yml
├── requirements.txt
├── config/
│   ├── settings.yaml
│   ├── keywords_seed.txt
│   └── stopwords.txt
├── core/
│   ├── discovery.py
│   ├── fetcher.py
│   ├── classifier.py
│   ├── keyword_extractor.py
│   ├── liveness.py
│   └── dedup.py
├── storage/
│   ├── models.py
│   ├── db.py
│   └── csv_exporter.py
├── orchestrator/
│   ├── scheduler.py
│   ├── worker.py
│   └── watchdog.py
├── cli/
│   └── main.py
├── logs/
└── data/
    ├── presence.db
    └── presence_gambling_sites.csv
```

---

## 3. Every file explained: purpose, inputs/outputs, and internal working

### `docker-compose.yml`
**Purpose:** brings up the self-hosted search backend the crawler depends on.
**Working:**
1. Starts a `redis` container (no exposed ports — internal use only). SearXNG needs Redis for its own result caching.
2. Starts a `searxng` container, exposed only on `127.0.0.1:8080` (not open to the network).
3. Mounts a `searxng/settings.yml` config that enables general web-search engines and disables image/video/map engines (they're noise for this use case).
4. On `docker compose up -d`, both containers stay running in the background — this is what `discovery.py` talks to.

---

### `config/settings.yaml`
**Purpose:** single place holding every tunable number/path so nothing is hardcoded in the Python files.
**Working:** loaded once at startup by `orchestrator/scheduler.py` into a plain dict/object, then passed down to every module that needs a value from it (fetch timeout, concurrency limit, thresholds, file paths). Changing a number here changes crawler behavior without touching code.

### `config/keywords_seed.txt`
**Purpose:** the starting point — one keyword or phrase per line, hand-written by you.
**Working:** read once at first startup by `storage/db.py`'s initialization routine, inserted into the `keywords` table with `source = 'seed'`. After that first load, the crawler never re-reads this file — all future keywords come from `keyword_extractor.py`.

### `config/stopwords.txt`
**Purpose:** a blocklist of generic words ("the", "click", "home", "login", etc.) that should never become search keywords even if they appear frequently on a confirmed gambling page.
**Working:** loaded into a Python `set()` at startup, checked by `keyword_extractor.py` before accepting any candidate new keyword.

---

### `core/dedup.py`
**Purpose:** makes sure the same site is never processed twice, no matter how many different URL forms point to it.
**Working, step by step:**
1. `normalize_url(raw_url)` — lowercases the scheme and host, strips `www.`, strips a trailing slash, strips default ports (`:80`, `:443`), and removes known tracking query parameters (`utm_source`, `fbclid`, etc.) while keeping meaningful ones.
2. `extract_domain(normalized_url)` — pulls out the registrable root domain (e.g. `bet365.com` from `sports.bet365.com/live`), using a public-suffix-aware library so multi-part TLDs like `.co.uk` are handled correctly.
3. `is_duplicate(url)` — normalizes the incoming URL, then does a single indexed lookup against the `urls` table. If a row already exists, returns `True` and the caller skips it entirely — no network request is ever made for a URL already known. This check always happens *before* `fetcher.py` is called, because a database lookup is thousands of times cheaper than an HTTP request.

---

### `core/discovery.py`
**Purpose:** turns one keyword into a list of candidate URLs by querying the search engine.
**Working, step by step:**
1. Takes a keyword string and the SearXNG base URL from config.
2. Calls `GET {searxng_base_url}/search?q={keyword}&format=json&pageno=1`.
3. Reads the results, collects every result URL.
4. Increments `pageno` and repeats, waiting `searxng_page_delay_seconds` between requests (so it doesn't hammer the local SearXNG instance).
5. Stops when either a page comes back with zero results, or `max_pages_per_keyword` is reached (a safety cap so one broad keyword can't loop forever).
6. Returns the full combined, de-duplicated list of raw URLs for that keyword back to the scheduler.
7. Marks that keyword's `last_used_at` and increments `times_used` in the database, so the scheduler naturally rotates to less-recently-used keywords next cycle instead of hammering the same ones.

---

### `core/fetcher.py`
**Purpose:** actually downloads a candidate page's HTML, safely and politely.
**Working, step by step:**
1. Before fetching, checks the `domains` table for that URL's domain: if it was fetched less than `per_domain_delay_seconds` ago, the request is delayed or requeued — this is what stops the crawler from hitting one site with a burst of requests.
2. Picks a random user-agent string from the configured pool (rotation, not deception — just avoiding naive bot-blocking on the first request).
3. Sends the HTTP GET with a timeout (`fetch_timeout_seconds`).
4. If the request times out or fails to connect, retries up to 2 times with exponential backoff. If it comes back with an HTTP error status (404, 500, etc.), that's treated as real information, not a glitch — it does *not* retry, it just records the status code.
5. Returns a result object: the URL, status code, raw HTML (if any), how long it took, and any error message.
6. Updates the `domains` table's `last_fetched_at` and `fetch_count` for that domain regardless of outcome.

---

### `core/classifier.py`
**Purpose:** decides whether a fetched page is genuinely a gambling/betting/casino site, and how confident that decision is.
**Working, step by step:**
1. Strips the HTML down to visible text plus key metadata (title, meta description).
2. Scans that text for three tiers of signal phrases, each with a different weight:
   - **Strong signals** (heavy weight): "deposit bonus", "free spins", "place a bet", "live dealer", "withdraw winnings", named licensing bodies (MGA, UKGC, Curaçao eGaming).
   - **Medium signals**: "casino", "slots", "poker", "sportsbook", "jackpot", "wager".
   - **Structural signals**: an 18+/age-gate interstitial, gambling-friendly payment processor branding, a "responsible gambling" footer link.
3. Sums the weighted matches into a single confidence score between 0 and 1.
4. Compares that score against `classification_threshold` from settings (set low, ~0.35, per the aggressive-recall decision) — if the score clears the bar, the page is marked `is_gambling = True`.
5. Returns the decision, the numeric score, and the specific list of matched phrases (`matched_signals`) — this list is stored even for rejected pages, so a human can later audit *why* something was or wasn't flagged.

---

### `core/keyword_extractor.py`
**Purpose:** turns a confirmed gambling page into new search keywords, so the crawler expands its own vocabulary.
**Working, step by step:**
1. Only ever runs on pages that `classifier.py` just marked `verified` — never on rejected or pending pages.
2. Strips HTML to plain text.
3. Runs a lightweight keyword-extraction algorithm (RAKE or TF-IDF — no heavy ML model needed at this scale) to rank candidate phrases by how distinctive they are to this page.
4. Filters the ranked list: drops anything already present in the `keywords` table, drops anything in `stopwords.txt`, drops single-character or overly generic terms.
5. Caps the result at `max_new_keywords_per_page` (default 5) — this cap exists specifically so one unusually text-heavy page can't flood the keyword pool with dozens of near-duplicate terms in one shot.
6. Returns the final short list, tagged with `source = 'extracted'` and `source_url` set to the page they came from, ready for `db.py` to insert.

---

### `core/liveness.py`
**Purpose:** periodically re-checks already-confirmed sites to catch ones that have gone offline or been parked/sold since they were first verified.
**Working, step by step:**
1. Runs on its own slower timer (`liveness_recheck_interval_hours`, default 72 — much less frequent than the main crawl loop), separate from new-URL discovery.
2. Pulls a batch of rows currently marked `verified`.
3. For each, sends a lightweight request (often just a HEAD or a fast GET) and checks: did it respond with a 200–399 status inside the timeout?
4. Also checks for "parked domain" patterns — near-empty page body, registrar-template markers, "this domain is for sale" text — since a parked domain returns 200 OK but isn't actually the gambling site anymore.
5. If either check fails, flips that row's status to `dead`. If it passes, just updates `last_checked_at` and leaves it `verified`.
6. Dead rows are automatically excluded from the next CSV export, since the exporter only reads `verified` rows.

---

### `storage/models.py`
**Purpose:** defines the shape of the data — the three tables and their columns — in one place, so every other file agrees on the schema.
**Working:** contains the table definitions for `keywords`, `urls`, and `domains` (see section 4 below for the full schema), including the allowed `status` values for `urls` (`pending`, `verified`, `rejected`, `dead`, `disputed`, `removed`) and the indexes needed for fast lookups (`urls.status`, `urls.domain`, `keywords.term`).

### `storage/db.py`
**Purpose:** the only file that talks directly to SQLite — every other module goes through this, never touching SQL directly.
**Working:** exposes simple functions that wrap the underlying queries:
- `get_pending_urls(limit)` — pulls the next batch of unprocessed URLs for the worker pool.
- `upsert_url(url, domain, keyword_id)` — inserts a new candidate URL as `pending`, or does nothing if it already exists.
- `mark_status(url_id, status, **fields)` — updates a URL's status plus any extra fields (confidence score, reasons, timestamps) in one call.
- `get_next_keywords(limit)` — returns the least-recently-used keywords, so the crawler rotates through its whole vocabulary instead of looping on the same few terms.
- `insert_keywords(list)` — inserts newly extracted keywords, silently skipping any that already exist (the `UNIQUE` constraint on `term` handles this at the database level).
- `get_verified_live_rows()` — the query the CSV exporter uses: every row that's `verified` and not `disputed`/`removed`.
- `touch_domain(domain)` — updates the `domains` table's fetch timestamp/count, used by the fetcher's rate limiter.
Uses `aiosqlite` so these calls don't block the async crawl loop while waiting on disk I/O.

### `storage/csv_exporter.py`
**Purpose:** produces the actual deliverable file.
**Working, step by step:**
1. Runs on its own independent timer (`csv_export_interval_minutes`, default 30) — completely decoupled from the crawl loop, so a slow export never stalls crawling and a busy crawl never delays an export.
2. Calls `db.get_verified_live_rows()`.
3. Writes them to `data/presence_gambling_sites.csv` with columns: `url, domain, confidence_score, first_seen_at, verified_at`.
4. Overwrites the file each run (it's a full snapshot of current state, not an append-only log) — the database is the permanent history; the CSV is just the current usable output.

---

### `orchestrator/scheduler.py`
**Purpose:** the conductor — the main loop that ties every other module together into one continuous process.
**Working, step by step (repeats forever while the service runs):**
1. Asks `db.py` for the next batch of least-recently-used keywords.
2. For each keyword, calls `discovery.search_keyword` to get candidate URLs.
3. Runs every candidate through `dedup.is_duplicate` — only genuinely new URLs get inserted as `pending`.
4. Hands the growing pool of `pending` URLs to a worker pool, sized by `max_concurrent_fetches` from settings — this is the concurrency control that keeps the crawler from overwhelming either its own machine or target sites.
5. In parallel (as separate async tasks, not blocking the main loop), it also kicks off `liveness.py`'s re-check batch and `csv_exporter.py`'s export whenever their respective timers come due.
6. Loops back to step 1 indefinitely.

### `orchestrator/worker.py`
**Purpose:** the unit of actual work done per URL — this is what runs, many times concurrently, inside the worker pool the scheduler manages.
**Working, step by step (one full cycle per URL):**
1. Calls `fetcher.fetch(url)`.
2. If the fetch failed outright (couldn't connect after retries), marks the URL `dead` and stops here.
3. If it succeeded, passes the HTML to `classifier.classify()`.
4. If classified as gambling: marks the URL `verified` with its confidence score and matched signals, then calls `keyword_extractor.extract_keywords()` on the same page and inserts any new keywords found.
5. If not classified as gambling: marks the URL `rejected` with the (low) score and matched signals — stored for auditability, and so this URL is never re-fetched.
6. Calls `db.touch_domain()` to update rate-limit bookkeeping regardless of outcome.

### `orchestrator/watchdog.py`
**Purpose:** keeps the whole thing alive and self-healing during 24/7 unattended operation.
**Working, step by step:**
1. On a timer, checks: has any new `pending` URL appeared in the last `stall_alert_minutes` (default 30)? If not, something upstream (discovery or SearXNG itself) has likely stalled — logs a warning and calls a stub `alert()` function (left for you to wire to email/webhook later).
2. Pings the local SearXNG endpoint to confirm it's reachable. If not, runs `docker compose restart searxng` as a subprocess call and logs the incident.
3. Wraps the scheduler's main loop task — if it ever crashes with an uncaught exception, the watchdog catches that, logs the full traceback, and restarts the loop rather than letting the whole process die silently.

---

### `cli/main.py`
**Purpose:** the human-facing control surface.
**Working:** a small command dispatcher exposing:
- `presence start` — boots the scheduler, watchdog, and exporter together as one long-running process (this is what you leave running 24/7).
- `presence stats` — queries `db.py` for counts by status, total keywords, and last export time, prints a quick summary.
- `presence add-keyword "<term>"` — lets you manually seed a keyword mid-run without restarting anything.
- `presence export-now` — forces an immediate CSV write instead of waiting for the next scheduled export.
- `presence dispute <url_id>` — flips a row to `disputed`, which removes it from the next CSV export without deleting its history — your correction mechanism for false positives under the aggressive classifier.

---

## 4. Database schema (reference)

**`keywords`**: `id, term (unique), source (seed|extracted), source_url, added_at, last_used_at, times_used`

**`urls`**: `id, url (unique, normalized), domain, discovered_via_keyword_id, status (pending|verified|rejected|dead|disputed|removed), confidence_score, classification_reasons (JSON), first_seen_at, last_checked_at, verified_at`

**`domains`**: `domain (primary key), last_fetched_at, fetch_count, confirmed_count`

---

## 5. Worked example — one keyword's full journey

Say the seed keyword is `"online casino bonus"`.

1. `scheduler.py` pulls this keyword (never used yet, so it's first in line).
2. `discovery.py` searches it against local SearXNG, walks 4 pages of results before a page comes back empty, returns 38 raw URLs.
3. `dedup.py` normalizes all 38, checks the database — 5 are already known, 33 are new. Those 33 get inserted as `pending`.
4. The worker pool picks up all 33 concurrently (well under the 200-concurrent limit).
5. For one of them, `casino-royale-xyz.com`: `fetcher.py` downloads the HTML successfully (200 OK, 340ms).
6. `classifier.py` finds "deposit bonus" (strong), "free spins" (strong), "slots" (medium), an 18+ age gate (structural) — weighted score comes out to 0.71, comfortably over the 0.35 aggressive threshold. Marked `verified`.
7. `keyword_extractor.py` runs on that same page's text, surfaces candidates like "welcome package", "wagering requirement", "crypto casino" — filters out anything already known or in stopwords, keeps the top 5, inserts them as new keywords with `source_url` pointing back to `casino-royale-xyz.com`.
8. Those 5 new keywords now sit in the `keywords` table, waiting their turn in a future scheduler cycle — the vocabulary just grew without you touching anything.
9. Meanwhile another one of the 33, `example-blog-about-odds.com`, scores only 0.12 (mentions "odds" once in a sports-news context, nothing else matches) — marked `rejected`, never fetched again.
10. 30 minutes later, `csv_exporter.py` runs, and `casino-royale-xyz.com` appears in `presence_gambling_sites.csv`.
11. 72 hours later, `liveness.py` re-checks it — still 200 OK, still not parked — `last_checked_at` updates, it stays in the CSV.
