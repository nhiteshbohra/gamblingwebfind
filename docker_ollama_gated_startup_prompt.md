# Prompt: Gate Docker (SearXNG) and Ollama startup per menu option in main.py

## Context
`main.py` has a 3-option interactive menu:
1. `keywordssearch` — needs **Docker** (SearXNG container). Does not use Ollama.
2. `checking_url` — needs **Ollama** (AI classifier). Does not use Docker.
3. `export_domains` — needs **neither**. Should stay instant.

Right now Docker is already correctly scoped: `start_searxng_docker()` in
`keywordssearch/searxng_search.py` only runs inside `run_search()`, i.e. only
when option 1 is chosen. Ollama was previously checked unconditionally on
every launch of `main.py` before the menu even showed — that's already been
moved so `check_ollama_status()` only fires inside `run_checking_url()`
(option 2).

## What's still missing
`check_ollama_status()` only *pings* Ollama and reports whether it's up — it
never starts it. So option 2 currently just warns and continues if Ollama is
offline, instead of starting it. Mirror the self-healing pattern already used
for MongoDB in `db/mongo_client.py::get_db()` (try connection → on failure,
`subprocess.Popen` the local binary → wait briefly → retry), applied to
Ollama, and confirm Docker's existing gating is airtight.

## Task
1. **Add `start_ollama_if_needed()`** in `checking_url/ai_classifier.py`:
   - Call `check_ollama_status()` first.
   - If it's already up, return immediately — do nothing.
   - If not, attempt `subprocess.Popen(["ollama", "serve"], stdout=DEVNULL, stderr=DEVNULL)` (configurable binary path via an `OLLAMA_AUTOSTART_PATH` env var, same style as `MONGOD_AUTOSTART_PATH`).
   - Poll `check_ollama_status()` every ~1s for up to ~15s (make this timeout an env var, e.g. `OLLAMA_AUTOSTART_TIMEOUT`).
   - Print a clear one-line status either way (`[+] Ollama: started` / `[!] Ollama: could not start automatically — start it manually`) and return a bool.
   - Never raise — this must be best-effort, same as the Mongo auto-start.

2. **Wire it into `run_checking_url()` in `main.py`**, replacing the current `_ensure_ollama_ready()` ping-only call with the new start-if-needed version, still called *after* the user picks a checking mode and *before* `checking_url.runner.run()` is invoked — so it only ever runs for option 2.

3. **Confirm Docker stays scoped to option 1 only** — no changes needed to `start_searxng_docker()` itself, just verify `run_searxng_search()` (option 1) is still the only call site, and that neither `run_checking_url()` (option 2) nor `run_export_domains()` (option 3) import or call anything from `keywordssearch/`.

4. **Leave option 3 (`run_export_domains()`) completely untouched** — no Docker check, no Ollama check, no added imports. It should still launch instantly.

## Acceptance criteria
- Running `python main.py` and picking option 3 does not touch Docker or Ollama at all — no subprocess calls, no HTTP pings, no added startup delay.
- Picking option 1 starts SearXNG's Docker container only if it isn't already running (existing behavior — confirm unchanged).
- Picking option 2 starts Ollama only if it isn't already running; if it's already up, no extra subprocess call is made.
- If Ollama fails to auto-start (binary not found, timeout), option 2 still proceeds — sites just get marked `unconfirmed` as documented, it doesn't crash the run.
- The API path (`api/routers/pipeline.py` → `checking_url.runner.run()`) is unaffected by this change — this is CLI-menu-only, since the API assumes the operator manages Ollama/Docker themselves in a server deployment.
