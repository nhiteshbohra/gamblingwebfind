# Presence — fix-everything instructions

Hand this whole file to your coding agent as-is. It covers two things:
(1) a standing process rule to prevent stale/wrong zips from being reviewed
again, and (2) the outstanding code fixes that still need to be confirmed
or applied. Do not skip the process rule — it's what makes every fix below
actually verifiable.

---

## PART 0 — Standing rule for every future round (do this every time, not just once)

1. After making ANY code changes, run:
   ```
   git add -A && git commit -m "<short description of what changed>"
   ```
2. Generate the deliverable zip FROM git, not from the raw folder:
   ```
   git archive -o gamblingwebfind_<short-hash>.zip HEAD
   ```
   (get `<short-hash>` from `git rev-parse --short HEAD`)
3. In your written report, always include the output of:
   ```
   git log -1 --oneline
   ```
   so the commit hash in your report can be matched against the hash in
   the zip's filename before any review starts.
4. Never name the zip `gamblingwebfind.zip` generically — always include
   the commit hash, so an old file can't be mistaken for the current one.
5. Every claim of "done" or "fixed" must be backed by pasted real command
   output (grep, git grep, test output) in your report — not a summary or
   description of what the code does. If you can't produce real output
   for something, say so explicitly rather than asserting it's done.

---

## PART 1 — Outstanding fixes (status unconfirmed due to a zip mismatch — verify or (re)apply each one, paste evidence for all 5)

### 1. Delete `core/discovery_daemon.py`
- Delete the file entirely.
- Remove the `discovery-daemon` subparser and its handling block from
  `cli/main.py`.
- Confirm: `git grep -n -E "discovery_daemon|DiscoveryDaemon|all domain links" -- "*.py"` returns nothing.

### 2. Make `get_settled_urls_set()` tier-aware
In `storage/db.py`:
```python
async def get_settled_urls_set(self, tier: str = None) -> set:
    query = "SELECT url FROM urls WHERE status IN ('dead','verified','rejected')"
    params = []
    if tier is not None:
        query += " AND verification_tier = ?"
        params.append(tier)
    async with self._connect() as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return {r[0] for r in rows}
```
In `cli/main.py`, the `verify-batch` command must call
`get_settled_urls_set(tier='strict')`, not the bare call.
- Confirm: `git grep -n "get_settled_urls_set" cli/main.py` shows
  `tier='strict'` in the call.

### 3. Merge `mark_status()` and `upsert_strict_result()` into `record_classification()`
In `storage/db.py`:
```python
async def record_classification(self, url: str, domain: str = None,
                                  status: str = 'pending',
                                  confidence_score: float = 0.0,
                                  reasons=None, tier: str = None,
                                  url_id: int = None):
    # INSERT ... ON CONFLICT(url) DO UPDATE, with 3-attempt retry + backoff
    # (reuse the retry pattern from the old upsert_strict_result)
```
Update every caller to use this one function:
- `orchestrator/worker.py`
- `core/liveness.py`
- `cli/main.py` (verify-batch)

Delete `mark_status()` and `upsert_strict_result()` once all callers are
migrated.
- Confirm: `git grep -n -E "def mark_status\b|def upsert_strict_result|def record_classification" storage/db.py`
  shows ONLY `record_classification`.

### 4. Remove inline SQL from `storage/excel_exporter.py`
Replace the exporter's own `WHERE status = '...'` queries with calls to
shared `db.py` helper functions (add `get_rejected_urls()`,
`get_dead_urls()`, `get_blocked_urls()` etc. if they don't already exist,
matching the existing `get_verified_live_rows()` pattern).
- Confirm: `git grep -n "WHERE status" storage/excel_exporter.py` returns
  nothing.

### 5. Delete `run_audit_checks.py` from the project root
It's a one-off diagnostic script, not part of the application, and isn't
imported anywhere.
- Confirm: `git grep -n "run_audit_checks"` returns nothing (or, if you
  want to keep it for your own use, move it into a `scripts/` folder
  clearly separate from the shipped app).

---

## PART 2 — Final proof, after all 5 are done

Run the resumability test end to end and paste the actual before/after DB
rows (not a description):

1. Insert or find a test URL with `status='verified'` and
   `verification_tier IS NULL` (simulating one already caught by the
   normal aggressive crawler).
2. Confirm it is NOT in `get_settled_urls_set(tier='strict')` — i.e. it
   gets picked up, not skipped, by `verify-batch`.
3. Run it through `verify-batch` and paste the row's DB state before and
   after — `verification_tier` should now read `'strict'`.

---

## PART 3 — Deliverable

- Commit all changes (`git add -A && git commit -m "..."`).
- Generate the zip via `git archive -o gamblingwebfind_<hash>.zip HEAD`.
- Report back with: `git log -1 --oneline` output, all 5 grep
  confirmations from Part 1, and the Part 2 before/after test output —
  all as real pasted command output.
