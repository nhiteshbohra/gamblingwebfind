"""
checking_url/restore_downgraded_blocklist.py

One-off cleanup: find domains that came from a trusted gambling list (GitHub
blocklist import / known-gambling import) but were later flipped to
regular/dead/for_sale by the pipeline reprocessing them -- the bug now guarded in
db.mongo_client.write_result -- and restore them to status="gambling", stamping
`pipeline_disputed` so a human can still eyeball them.

Dry-run by default. Add --apply to write.

    python -m checking_url.restore_downgraded_blocklist            # count only
    python -m checking_url.restore_downgraded_blocklist --apply    # restore
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from db.mongo_client import IST, checked_domains, _is_trusted_gambling  # noqa: E402

_FLIPPED = ("regular", "dead", "for_sale")


def _candidates():
    # decided_by / reason / source markers, any of which means "trusted import"
    q = {
        "status": {"$in": list(_FLIPPED)},
        "$or": [
            {"decided_by": "external_blocklist"},
            {"reason": "Known gambling — confirmed true positive"},
            {"source": {"$regex": r"^(blocklist:|imported from )"}},
        ],
    }
    return checked_domains().find(q, {"status": 1, "decided_by": 1, "reason": 1, "source": 1})


def run(apply: bool) -> dict:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    seen = {s: 0 for s in _FLIPPED}
    ids: list[str] = []
    for doc in _candidates():
        # _is_trusted_gambling expects status=="gambling"; here status is the flipped one,
        # so re-check the markers directly (same logic, status clause relaxed).
        marker_ok = (
            doc.get("decided_by") == "external_blocklist"
            or (doc.get("reason") or "").strip() == "Known gambling — confirmed true positive"
            or str(doc.get("source") or "").startswith(("blocklist:", "imported from "))
        )
        if not marker_ok:
            continue
        seen[doc["status"]] = seen.get(doc["status"], 0) + 1
        ids.append(doc["_id"])

    print(f"trusted-import domains currently flipped away from gambling: {len(ids)}")
    for s, n in seen.items():
        if n:
            print(f"  {s:9}: {n}")

    if not ids:
        return {"restored": 0, **seen}
    if not apply:
        print("\n(dry run -- re-run with --apply to restore these to status='gambling')")
        print("sample:", ", ".join(ids[:10]))
        return {"restored": 0, **seen}

    res = checked_domains().update_many(
        {"_id": {"$in": ids}},
        {"$set": {
            "status": "gambling",
            "reason": "Known blocklist (restored after pipeline downgrade)",
            "pipeline_disputed": "restored",
            "pipeline_disputed_at": today,
        }},
    )
    print(f"\nrestored {res.modified_count} domains to status='gambling'")
    return {"restored": res.modified_count, **seen}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    run(ap.parse_args().apply)
