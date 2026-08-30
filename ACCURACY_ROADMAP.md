# Accuracy Roadmap

Working plan, in order. Don't skip ahead — each phase's output is the next phase's input.

**Ollama stays.** Nothing here replaces the heuristic classifier (`checking_url/classifier.py`)
or the Ollama AI layer (`checking_url/ai_classifier.py` + `Modelfile`/`Modelfile.validator`).
The two layers keep their existing jobs: the heuristic layer decides the easy/obvious cases
fast and free, Ollama decides the cases the heuristic can't (`needs_ai` escalation, tiebreaker,
vision fallback). This plan evolves *both* layers side by side using real, human-verified data
instead of guessing — it does not remove either one.

---

## Phase 1 — Manually finalize the gambling corpus (current step)

Goal: walk the ~17,826 domains already marked `gambling` (and the ~592 marked `regular`) and
manually confirm each one, correcting anything wrong, so the corpus itself is trustworthy
before it's used for anything downstream.

1. **Audit first, so you're not reviewing gaps.**
   ```
   python -m project_sup.screenshot_audit          # screenshot_taken=True but file missing on disk
   ```
2. **Sort the existing screenshot corpus into folders by current MongoDB status**, so review
   is a visual folder-by-folder pass instead of a spreadsheet:
   ```
   python -m project_sup.sort_screenshots_by_mongo_status --input "<a copy, never output/screenshots/ itself>"
   ```
   This produces `gambling/` and `false/` subfolders, trusting whatever the pipeline already
   decided — it's the starting point for review, not the correction.
3. **Manually review each folder.** Anything in `gambling/` that isn't actually an online
   real-money gambling site (hotel-with-onsite-casino, review/affiliate site, false positive
   from a bug) gets pulled out. Anything in `false/` that's actually gambling gets flagged too.
4. **Tooling gap — not built yet:** there is currently no script that writes a manual
   correction (a file you moved between folders) back into MongoDB's `status` field — the
   old per-domain confirm/verdict mechanism (`project_sup/review_queue.py`'s `confirm_domain`)
   was intentionally removed because no human reviews *routine, ongoing* classification
   decisions. This is different: a one-time corpus-finalization pass, not per-domain review of
   new incoming domains. When you're ready to write corrections back, ask for a small script
   that's the mirror image of `sort_screenshots_by_mongo_status.py` (folder → MongoDB, instead
   of MongoDB → folder).

**Exit condition for Phase 1:** every domain in the `gambling`/`regular` corpus has been
manually eyeballed at least once, and any misclassification found is corrected.

---

## Phase 2 — Build the real evaluation set (only after Phase 1)

Doing this before Phase 1 would measure accuracy against a corpus you already know has errors
in it — the eval set has to be sampled from a corpus you trust.

1. ```
   python -m checking_url.build_eval_set --per-bucket 20 --out eval_set.csv
   ```
   Pulls a stratified sample across every decision path (`domain_anchor_lock`,
   `heuristic_score_lock`, `ai_round1`, `tiebreaker_resolved`, `validator_confirmed`,
   `regular_zero_keywords`, `regular_parked_for_sale`, etc.), excluding the circular label
   sources (`external_blocklist` imports, `known_gambling_runner` confirmations) that never
   went through the classifier at all.
2. Fill in `human_label` for each row (`gambling` / `regular` / `unsure`) — real ground truth,
   done once Phase 1 makes the source corpus trustworthy.
3. ```
   python -m checking_url.score_eval_set --in eval_set.csv --by-bucket
   ```
   Gives real accuracy/precision/recall/F1, overall and per decision path. This is the first
   honest accuracy number this project will have.

---

## Phase 3 — Evolve both layers using what Phase 2 found

The per-bucket breakdown from `score_eval_set.py` tells you *which layer* is responsible for
each error, so you fix the right one:

- **Heuristic layer weak** (`gambling_domain_anchor_lock`, `heuristic_score_lock` buckets
  scoring badly) → mine new signal words from the corpus and hand-add the genuinely
  gambling-specific ones to `STRONG_GAMBLING_SIGNALS`/`WEAK_GAMBLING_SIGNALS` in
  `checking_url/classifier.py`:
  ```
  python -m project_sup.mine_keyword_candidates --sample 1500 --top 40
  ```
  Candidates only — every addition is human-reviewed before going in, same as the
  `ibinfra.in`/`pokerledger.net`/`casinobonuschecker.com` bug fixes this session, just
  surfaced automatically instead of by eye.
- **AI layer weak** (`ai_round1`, `ai_round2_challenge`, `tiebreaker_resolved`,
  `validator_confirmed`/`validator_override` buckets scoring badly) → tune the prompts in
  `Modelfile` / `Modelfile.validator`, not the Python heuristics. This is the layer Ollama
  owns — it's *supposed* to be the one making judgment calls the rules can't, so its accuracy
  gets improved by better prompting, not by trying to route around it.
- **Lock every fix with a regression test** in `tests/test_classifier_accuracy.py` so it can't
  silently regress later.

## Ongoing cadence

Re-run `build_eval_set.py` periodically for a fresh sample as the corpus grows — it refuses to
overwrite a CSV that already has `human_label` values filled in (`--force` to override), so old
labeled rows aren't lost. Each pass: label → score → fix the responsible layer → regression
test → re-sample. That loop is what "evolving the model over time" actually means here — not a
one-off retrain, a repeating measurement cycle across both the heuristic rules and the Ollama
prompts.
