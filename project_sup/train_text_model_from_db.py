r"""
project_sup/train_text_model_from_db.py
======================================
Train checking_url/ml_text_model.py on the page text this pipeline already stored.

Every `needs_ai` verdict persists `ai_context` (title / meta / CTA buttons / body
excerpt -- the exact text shown to the Ollama Analyst). That is a ready-made,
non-scraped corpus: ~2.6k gambling + ~0.7k non-gambling pages whose LABEL came
from the LLM challenge, not from the keyword heuristic -> training a TF-IDF model
on the TEXT is not circular the way training on `domain_anchor_strong` rows would
be. `false_gambling` rows are folded in as (hard) negatives -- a page the pipeline
flagged and a human/After-review cleared is exactly a non-gambling example worth
learning from.

    python -m project_sup.train_text_model_from_db                 # dry: show corpus balance only
    python -m project_sup.train_text_model_from_db --train         # train + write the .pkl
    python -m project_sup.train_text_model_from_db --train --dump output/text_rows.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=_ROOT / ".env")

from checking_url.ml_text_model import _DEFAULT_MODEL, train_from_rows  # noqa: E402
from db.mongo_client import get_db  # noqa: E402

# status -> training label. false_gambling is a confirmed NON-gambling page the
# pipeline once over-flagged: a hard negative, not a positive.
_POS_STATUS = {"gambling"}
_NEG_STATUS = {"regular", "false_gambling"}


def _row_text(ai: dict) -> str:
    parts = [
        ai.get("title") or "",
        ai.get("meta_description") or "",
        " ".join(ai.get("cta_buttons") or []),
        ai.get("body_excerpt") or "",
    ]
    return "  ".join(p for p in parts if p).strip()


def load_rows() -> list[dict]:
    col = get_db()["checked_domains"]
    q = {
        "ai_context.body_excerpt": {"$exists": True, "$nin": [None, ""]},
        "status": {"$in": sorted(_POS_STATUS | _NEG_STATUS)},
    }
    rows: list[dict] = []
    for d in col.find(q, {"domain": 1, "status": 1, "decided_by": 1, "ai_context": 1}):
        text = _row_text(d.get("ai_context") or {})
        if len(text) < 40:
            continue
        rows.append({
            "text": text,
            "label": "gambling" if d["status"] in _POS_STATUS else "regular",
            "source": d.get("decided_by") or "",
            "domain": d.get("domain") or "",
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", action="store_true", help="actually train + write the model (default: just report)")
    ap.add_argument("--out", default=str(_DEFAULT_MODEL))
    ap.add_argument("--dump", default="", help="also write the rows as JSONL here")
    ap.add_argument("--min-per-class", type=int, default=200)
    a = ap.parse_args()

    rows = load_rows()
    n_pos = sum(r["label"] == "gambling" for r in rows)
    n_neg = len(rows) - n_pos
    print(f"corpus: {len(rows)} rows  ({n_pos} gambling, {n_neg} non-gambling)")
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    print("by decided_by:", dict(sorted(by_src.items(), key=lambda kv: -kv[1])))

    if a.dump:
        Path(a.dump).parent.mkdir(parents=True, exist_ok=True)
        Path(a.dump).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        print(f"wrote {a.dump}")

    if not a.train:
        print("\n(dry run -- pass --train to build the model)")
        return 0

    metrics = train_from_rows(rows, out_path=a.out, min_per_class=a.min_per_class)
    print("\nmetrics:", json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
