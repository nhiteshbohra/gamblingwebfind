"""
project_sup/review_queue.py — Prioritized human-review queue generator.

Moved here from checking_url/ -- it's a standalone analysis/support tool (no other module
imports it, not wired into main.py's menu), same category as build_eval_set.py/
score_eval_set.py/mine_keyword_candidates.py. checking_url/ is reserved for the live
per-domain pipeline stages this ranks results FROM, not for tooling that reads them after
the fact.

At 18,000+ domains, checking every result by hand isn't viable, and it isn't necessary: the
verdicts most likely to be wrong are a small, identifiable slice — the ones decided with the
least scrutiny or the most internal disagreement. This script ranks and exports that slice
in BOTH directions, using the confidence/category/decided_by/ai_context fields the pipeline
already writes to every domain:

  GAMBLING verdicts (false positives here are the costly direction — a real business banned):
    Tier 1 — heuristic-only lock with thin evidence (<=2 keywords), never saw AI at all
    Tier 2 — any verdict with confidence < 0.7
    Tier 3 — AI needed a validator override or tie-breaker to arbitrate (confidence < 0.85)

  REGULAR verdicts (false negatives here are a live gambling site left unbanned):
    Tier 1 — a gambling-TLD domain (.bet/.casino/.poker/.bingo/.lotto) that still landed regular
    Tier 2 — heuristic-only "0 keywords matched" (zero visible signal — worth a second,
             browser-rendered look in case it's a JS/canvas-heavy site)
    Tier 3 — AI needed a validator/tie-breaker arbitration and still landed regular

Sampling guidance for anything past Tier 1: checking 100% of Tier 1 is worth the time (it's
usually a small list). For Tiers 2-3, a random 5-10% sample is normally enough to catch a
systemic problem without hand-checking thousands of individually low-risk verdicts.

Usage:
    python -m project_sup.review_queue                       # print summary counts
    python -m project_sup.review_queue --direction gambling --tier 1 --limit 50
    python -m project_sup.review_queue --export review.csv    # export everything, all tiers
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(_ROOT) / ".env")

from db.mongo_client import get_db, checked_domains
from export_domains.screenshot import find_screenshot_path

GAMBLING_TLDS = (".bet", ".casino", ".poker", ".bingo", ".lotto")


def _keyword_count_from_reason(reason: str) -> int | None:
    """Heuristic-only writes leave 'reason' as '<N> keywords matched' — extract N."""
    if not reason:
        return None
    m = re.match(r"^\s*(\d+)\s+keywords matched", reason)
    return int(m.group(1)) if m else None


def _tier_gambling(doc: dict) -> int | None:
    decided_by = doc.get("decided_by", "")
    confidence = doc.get("confidence")
    category = doc.get("category", "")
    kw_count = _keyword_count_from_reason(doc.get("reason", ""))

    # Strongest signal of all: an independent second look at the actual screenshot (OCR +
    # vision model, see screenshot_visual_audit.py) found no gambling evidence in the
    # image itself. Two disagreeing evidence sources on the same page is worth a human's
    # eyes before either the text verdict or the image.
    if doc.get("visual_confirmed") is False:
        return 1
    if decided_by == "heuristic_score" and kw_count is not None and kw_count <= 2:
        return 1
    if confidence is not None and confidence < 0.7:
        return 2
    if category in ("validator_override", "tiebreaker_resolved") and (confidence is None or confidence < 0.85):
        return 3
    return None


def _tier_regular(doc: dict) -> int | None:
    domain = doc.get("domain", "") or doc.get("_id", "")
    decided_by = doc.get("decided_by", "")
    category = doc.get("category", "")
    kw_count = _keyword_count_from_reason(doc.get("reason", ""))

    if any(domain.lower().endswith(t) for t in GAMBLING_TLDS):
        return 1
    if decided_by == "heuristic_score" and kw_count == 0:
        return 2
    if category in ("validator_uncertain", "tiebreaker_resolved"):
        return 3
    return None


def build_review_queue(direction: str = "both", tier: int | None = None, limit: int = 0) -> list[dict]:
    """direction: 'gambling', 'regular', or 'both'."""
    rows = []
    statuses = []
    if direction in ("gambling", "both"):
        statuses.append("gambling")
    if direction in ("regular", "both"):
        statuses.append("regular")

    query = {"status": {"$in": statuses}}

    for doc in checked_domains().find(query):
        t = _tier_gambling(doc) if doc.get("status") == "gambling" else _tier_regular(doc)
        if t is None:
            continue
        if tier is not None and t != tier:
            continue
        rows.append({
            "domain": doc.get("domain") or doc.get("_id"),
            "url": doc.get("url", ""),
            "status": doc.get("status"),
            "tier": t,
            "confidence": doc.get("confidence"),
            "category": doc.get("category"),
            "decided_by": doc.get("decided_by"),
            "reason": doc.get("reason", ""),
            "visual_confirmed": doc.get("visual_confirmed"),
            "visual_evidence": doc.get("visual_evidence"),
        })

    rows.sort(key=lambda r: (r["tier"], r["confidence"] if r["confidence"] is not None else 1.0))
    if limit:
        rows = rows[:limit]
    return rows


def find_screenshot(domain: str, url: str) -> str | None:
    """Locate the already-captured screenshot for this domain on disk, if any."""
    screenshot_dir = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))
    return find_screenshot_path(url or f"https://{domain}", domain, screenshot_dir)


def show_evidence(domain: str):
    """Print everything needed to verify this ONE domain without visiting the live site
    yourself: the AI's own cited evidence, exactly what page text/CTAs it was shown, and
    where the already-captured screenshot lives on disk. Verifying means reading this
    evidence package and judging whether it's convincing — not re-investigating from
    scratch, and not needing gambling-industry expertise to do so.
    """
    doc = checked_domains().find_one({"_id": domain}) or checked_domains().find_one({"domain": domain})
    if not doc:
        print(f"'{domain}' not found in checked_domains.")
        return

    print("\n" + "=" * 70)
    print(f"EVIDENCE PACKAGE — {domain}")
    print("=" * 70)
    print(f"URL:          {doc.get('url')}")
    print(f"Status:       {doc.get('status')}")
    print(f"Confidence:   {doc.get('confidence')}")
    print(f"Category:     {doc.get('category')}")
    print(f"Decided by:   {doc.get('decided_by')}")
    print(f"IP / ASN:     {doc.get('ip')} / {doc.get('asn')}")
    print(f"\nAI's stated reason:\n  {doc.get('reason')}")

    if "visual_confirmed" in doc:
        print(f"\nScreenshot visual audit: confirmed={doc.get('visual_confirmed')} "
              f"(confidence={doc.get('visual_confidence')}, checked {doc.get('visual_checked_at')})")
        print(f"  Evidence: {doc.get('visual_evidence')}")

    ctx = doc.get("ai_context")
    if ctx:
        print(f"\n--- What the AI actually read from the live page ---")
        print(f"Title:        {ctx.get('title')}")
        print(f"Meta desc:    {ctx.get('meta_description')}")
        print(f"CTA buttons:  {ctx.get('cta_buttons')}")
        print(f"Screenshot used in classification: {ctx.get('screenshot_used')}")
        print(f"Body excerpt:\n  {ctx.get('body_excerpt', '')[:800]}")
    else:
        print("\n(No ai_context saved — this domain was decided by the heuristic layer alone, never sent to AI.)")

    shot = find_screenshot(domain, doc.get("url", ""))
    print(f"\nScreenshot on disk: {shot or '(none found — status may not be gambling, or capture failed)'}")
    print("=" * 70 + "\n")


def print_summary():
    counts = {"gambling": {1: 0, 2: 0, 3: 0}, "regular": {1: 0, 2: 0, 3: 0}}
    totals = {"gambling": 0, "regular": 0}
    for doc in checked_domains().find({"status": {"$in": ["gambling", "regular"]}}, {"status": 1, "decided_by": 1, "confidence": 1, "category": 1, "reason": 1, "domain": 1}):
        status = doc.get("status")
        totals[status] = totals.get(status, 0) + 1
        t = _tier_gambling(doc) if status == "gambling" else _tier_regular(doc)
        if t is not None:
            counts[status][t] += 1

    print("\n" + "=" * 60)
    print("REVIEW QUEUE SUMMARY")
    print("=" * 60)
    for status in ("gambling", "regular"):
        flagged = sum(counts[status].values())
        print(f"\n{status.upper()} — {totals.get(status, 0)} total, {flagged} flagged for review ({100*flagged/max(1,totals.get(status,1)):.1f}%)")
        for t in (1, 2, 3):
            print(f"  Tier {t}: {counts[status][t]}")
    print("\nRun with --direction/--tier/--limit to list specific rows, --evidence DOMAIN to inspect one,")
    print("or --export to write a CSV.")
    print("=" * 60 + "\n")


def export_csv(rows: list[dict], path: str):
    if not rows:
        print("No rows to export.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} rows to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prioritized review queue for checked_domains")
    parser.add_argument("--direction", choices=["gambling", "regular", "both"], default="both")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--export", type=str, default=None, help="Write results to this CSV path instead of printing")
    parser.add_argument("--evidence", type=str, default=None, metavar="DOMAIN", help="Show the full evidence package for one domain")
    args = parser.parse_args()

    get_db()

    if args.evidence:
        show_evidence(args.evidence)
    elif args.export:
        rows = build_review_queue(direction=args.direction, tier=args.tier, limit=args.limit)
        export_csv(rows, args.export)
    elif args.direction == "both" and args.tier is None and args.limit == 0:
        print_summary()
    else:
        rows = build_review_queue(direction=args.direction, tier=args.tier, limit=args.limit or 50)
        for r in rows:
            print(f"[T{r['tier']}] {r['status']:9s} conf={r['confidence']!s:5s} {r['domain']:35s} {r['reason'][:80]}")
