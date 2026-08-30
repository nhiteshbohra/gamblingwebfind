"""
checking_url/build_eval_set.py -- Non-circular evaluation-set generator.

WHY THIS EXISTS: this codebase has no held-out labeled evaluation set anywhere, and no
measured accuracy/precision/recall/F1 number -- tests/test_classifier_accuracy.py is a
regression suite of 18 hand-crafted synthetic incident cases, not a statistical sample of
real domains. Before chasing any target accuracy number (e.g. the ~99% reported by the
"Hybrid Multimodal Data Fusion" gambling-detection paper this project was benchmarked
against), you need a real baseline measured on real, honestly-labeled data. That's what
this script prepares -- a candidate list for a HUMAN to label, not an automatic label.

NON-CIRCULARITY: two sources of "gambling" labels in checked_domains never actually went
through classify()/classify_with_challenge() -- they were imported pre-labeled:
  1. list_fetcher/keysfetch_from_txt.py writes decided_by="external_blocklist"
  2. checking_url/known_gambling_runner.py writes reason="Known gambling — confirmed
     true positive" with no decided_by at all (it only verifies liveness + screenshots)
Including those in an accuracy measurement would partly just be checking "did we copy the
blocklist correctly," not "did our classifier correctly detect gambling" -- this script
excludes both by construction.

STRATIFICATION: pulls a bounded, roughly balanced sample across the decision paths and
risk categories this codebase itself already tracks (decided_by/category/reason), so the
eval set actually stresses the cases most likely to be wrong -- not just an arbitrary
random sample dominated by whatever's most common in the database.

USAGE:
    python -m checking_url.build_eval_set --per-bucket 20 --out eval_set.csv
    python -m checking_url.build_eval_set --per-bucket 20 --out eval_set.csv --seed 42

OUTPUT: a CSV with the pipeline's own current verdict/evidence alongside blank columns
for a human to fill in (human_label, human_confidence, human_notes, reviewed_by,
reviewed_at). Nothing in this script writes anything back to MongoDB.

NEXT STEP: after a human fills in `human_label` for each row (gambling / regular /
unsure), run:
    python -m checking_url.score_eval_set --in eval_set.csv
to get real accuracy/precision/recall/F1, per-class and overall, comparing the pipeline's
`current_status` against `human_label`.
"""
import argparse
import csv
import random
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(_ROOT) / ".env")

from db.mongo_client import get_db, checked_domains

try:
    from checking_url.classifier import is_gambling_domain
except ImportError:
    def is_gambling_domain(url):
        return False, ""

# Circular / non-classifier-derived label sources -- always excluded (see module docstring).
_CIRCULAR_REASON_EXACT = "Known gambling — confirmed true positive"


def _is_circular(doc: dict) -> bool:
    if doc.get("decided_by") == "external_blocklist":
        return True
    if str(doc.get("reason", "")).strip() == _CIRCULAR_REASON_EXACT:
        return True
    return False


# Stratification buckets: (label, bucket_name, mongo_filter). Buckets are checked in order
# and a doc lands in the FIRST bucket it matches, so put more specific/higher-risk buckets
# before generic ones (e.g. "gambling-anchor domain landed regular" before generic "regular").
def _build_buckets():
    return [
        # --- gambling-labeled buckets (false positives are the costly direction here) ---
        ("gambling", "gambling_domain_anchor_lock",
         {"status": "gambling", "decided_by": {"$in": ["domain_anchor_strong"]}}),
        ("gambling", "gambling_heuristic_score_lock",
         {"status": "gambling", "decided_by": {"$in": ["heuristic_score", None]}}),
        ("gambling", "gambling_ai_round1",
         {"status": "gambling", "decided_by": "ai_round1"}),
        ("gambling", "gambling_ai_challenge_or_validator",
         {"status": "gambling", "decided_by": {"$in": ["ai_round2_challenge", "validator_confirmed", "validator_override"]}}),
        ("gambling", "gambling_tiebreaker",
         {"status": "gambling", "decided_by": "tiebreaker_resolved"}),
        ("gambling", "gambling_low_confidence",
         {"status": "gambling", "confidence": {"$lt": 0.7}}),

        # --- regular-labeled buckets (false negatives here = a live gambling site left unbanned) ---
        ("regular", "regular_zero_keywords",
         {"status": "regular", "reason": {"$regex": "^0 keywords matched"}}),
        ("regular", "regular_trusted_allowlist",
         {"status": "regular", "decided_by": "trusted_allowlist"}),
        ("regular", "regular_parked_for_sale",
         {"status": "regular", "reason": {"$regex": "Parked|For-Sale", "$options": "i"}}),
        ("regular", "regular_ai_rejected",
         {"status": "regular", "decided_by": {"$in": ["ai_round1", "ai_round2_challenge", "validator_confirmed", "validator_override", "tiebreaker_resolved"]}}),
        ("regular", "regular_cloaking_suspected",
         {"status": "regular", "category": "cloaking_suspected"}),
        ("regular", "regular_other_heuristic",
         {"status": "regular"}),
    ]


def _sample(cursor_docs: list, k: int, rng: random.Random) -> list:
    if len(cursor_docs) <= k:
        return cursor_docs
    return rng.sample(cursor_docs, k)


def _has_existing_labels(out_path: str) -> bool:
    """True if out_path already exists and has at least one filled-in human_label —
    used to refuse a silent overwrite that would destroy in-progress human labeling work
    when this is re-run periodically (e.g. as a scheduled refresh)."""
    if not Path(out_path).exists():
        return False
    try:
        with open(out_path, newline="", encoding="utf-8") as f:
            return any((row.get("human_label") or "").strip() for row in csv.DictReader(f))
    except Exception:
        return False


def build(per_bucket: int, out_path: str, seed: int | None, force: bool = False):
    if not force and _has_existing_labels(out_path):
        print(
            f"[!] Refusing to overwrite {out_path} — it already has human_label values filled "
            f"in. Re-running this would destroy that labeling work. Either pick a different "
            f"--out path, or pass --force if you're certain you want to discard the existing "
            f"labels."
        )
        return
    get_db()
    rng = random.Random(seed)
    seen_ids = set()
    rows = []
    bucket_counts = {}

    for label, bucket_name, mongo_filter in _build_buckets():
        query = dict(mongo_filter)
        query["status"] = label
        docs = list(checked_domains().find(query))
        # De-dup against earlier (more specific) buckets and drop circular sources.
        candidates = [d for d in docs if d.get("_id") not in seen_ids and not _is_circular(d)]
        chosen = _sample(candidates, per_bucket, rng)
        for d in chosen:
            seen_ids.add(d.get("_id"))
            is_anchor, anchor_sig = is_gambling_domain(d.get("url") or f"https://{d.get('_id')}")
            rows.append({
                "domain": d.get("_id"),
                "url": d.get("url") or f"https://{d.get('_id')}",
                "current_status": d.get("status"),
                "current_reason": d.get("reason"),
                "decided_by": d.get("decided_by"),
                "category": d.get("category"),
                "confidence": d.get("confidence"),
                "stratum_bucket": bucket_name,
                "gambling_domain_anchor": is_anchor,
                "human_label": "",
                "human_confidence": "",
                "human_notes": "",
                "reviewed_by": "",
                "reviewed_at": "",
            })
        bucket_counts[bucket_name] = len(chosen)

    rng.shuffle(rows)  # so a reviewer working top-down doesn't do all of one bucket first

    fieldnames = [
        "domain", "url", "current_status", "current_reason", "decided_by", "category",
        "confidence", "stratum_bucket", "gambling_domain_anchor",
        "human_label", "human_confidence", "human_notes", "reviewed_by", "reviewed_at",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} candidate domains to {out_path}\n")
    print("Per-bucket counts (target was {} each):".format(per_bucket))
    for name, count in bucket_counts.items():
        flag = "  <-- fewer than requested, bucket is smaller than per-bucket target" if count < per_bucket else ""
        print(f"  {name:40s} {count:4d}{flag}")
    excluded_note = (
        "\nExcluded by construction: domains with decided_by=='external_blocklist' or "
        "reason=='Known gambling — confirmed true positive' (imported pre-labels that "
        "never went through classify()/classify_with_challenge() -- including them would "
        "make any accuracy measurement partly circular)."
    )
    print(excluded_note)
    print(
        "\nNEXT STEP: open the CSV, visit each domain (safely -- a sandboxed/disposable "
        "browser session is strongly recommended for real gambling URLs, not your normal "
        "browser profile), and fill in human_label as one of: gambling / regular / unsure. "
        "Leave 'unsure' cases out of the accuracy computation, or track them separately -- "
        "they're genuinely ambiguous, not pipeline errors.\n"
        "Then run: python -m checking_url.score_eval_set --in " + out_path
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a non-circular, stratified evaluation-set CSV for manual labeling")
    parser.add_argument("--per-bucket", type=int, default=20, help="Max domains to sample per stratification bucket (default 20)")
    parser.add_argument("--out", type=str, default="eval_set.csv", help="Output CSV path")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sampling")
    parser.add_argument("--force", action="store_true", help="Overwrite --out even if it already has human_label values filled in")
    args = parser.parse_args()
    build(args.per_bucket, args.out, args.seed, args.force)
