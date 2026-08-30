"""
checking_url/score_eval_set.py -- Compute real accuracy/precision/recall/F1 from a
human-labeled CSV produced by checking_url/build_eval_set.py.

USAGE:
    python -m checking_url.score_eval_set --in eval_set.csv
    python -m checking_url.score_eval_set --in eval_set.csv --by-bucket

Rows with human_label blank or "unsure" are excluded from scoring and reported
separately (they are not pipeline errors -- they're either not-yet-reviewed or
genuinely ambiguous cases).

Positive class = "gambling" (this matches how the paper being benchmarked against
reports precision/recall, and is the direction that actually matters operationally:
missing a live gambling site is a false negative, banning a legitimate site is a
false positive).
"""
import argparse
import csv
from collections import defaultdict


def _norm(label: str) -> str:
    label = (label or "").strip().lower()
    if label in ("gambling", "g", "yes", "1", "true"):
        return "gambling"
    if label in ("regular", "r", "no", "0", "false", "not_gambling", "not gambling"):
        return "regular"
    return ""


def score(rows: list) -> dict:
    tp = fp = fn = tn = 0
    for r in rows:
        pred = _norm(r["current_status"])
        actual = _norm(r["human_label"])
        if not actual or actual == "unsure":
            continue
        if pred == "gambling" and actual == "gambling":
            tp += 1
        elif pred == "gambling" and actual == "regular":
            fp += 1
        elif pred == "regular" and actual == "gambling":
            fn += 1
        elif pred == "regular" and actual == "regular":
            tn += 1
    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total else float("nan")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if (precision == precision and recall == recall and (precision + recall) > 0)
          else float("nan"))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "total": total,
            "accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1}


def _fmt(v):
    return "n/a" if v != v else f"{v:.4f}"


def _print_block(title: str, m: dict, false_positives: list, false_negatives: list):
    print(f"\n=== {title} ===")
    print(f"  n scored          : {m['total']}")
    print(f"  TP / FP / FN / TN : {m['tp']} / {m['fp']} / {m['fn']} / {m['tn']}")
    print(f"  Accuracy          : {_fmt(m['accuracy'])}")
    print(f"  Precision (gambling): {_fmt(m['precision'])}")
    print(f"  Recall (gambling)   : {_fmt(m['recall'])}")
    print(f"  F1 (gambling)       : {_fmt(m['f1'])}")
    if false_positives:
        print(f"  False positives (pipeline said gambling, human said regular): {len(false_positives)}")
        for d in false_positives[:15]:
            print(f"    - {d}")
        if len(false_positives) > 15:
            print(f"    ... and {len(false_positives) - 15} more")
    if false_negatives:
        print(f"  False negatives (pipeline said regular, human said gambling): {len(false_negatives)}")
        for d in false_negatives[:15]:
            print(f"    - {d}")
        if len(false_negatives) > 15:
            print(f"    ... and {len(false_negatives) - 15} more")


def main(path: str, by_bucket: bool):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    unreviewed = [r for r in rows if not _norm(r.get("human_label", ""))
                  and (r.get("human_label", "").strip().lower() != "unsure")]
    unsure = [r for r in rows if r.get("human_label", "").strip().lower() == "unsure"]
    scored_rows = [r for r in rows if _norm(r.get("human_label", ""))]

    overall = score(scored_rows)
    fps = [r["domain"] for r in scored_rows
           if _norm(r["current_status"]) == "gambling" and _norm(r["human_label"]) == "regular"]
    fns = [r["domain"] for r in scored_rows
           if _norm(r["current_status"]) == "regular" and _norm(r["human_label"]) == "gambling"]
    _print_block("OVERALL", overall, fps, fns)

    print(f"\n  ({len(unreviewed)} rows not yet labeled, {len(unsure)} labeled 'unsure' -- excluded above)")

    if by_bucket:
        buckets = defaultdict(list)
        for r in scored_rows:
            buckets[r.get("stratum_bucket", "unknown")].append(r)
        for name, brows in sorted(buckets.items()):
            m = score(brows)
            bfps = [r["domain"] for r in brows
                    if _norm(r["current_status"]) == "gambling" and _norm(r["human_label"]) == "regular"]
            bfns = [r["domain"] for r in brows
                    if _norm(r["current_status"]) == "regular" and _norm(r["human_label"]) == "gambling"]
            _print_block(f"bucket: {name}", m, bfps, bfns)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score a human-labeled eval_set.csv against the pipeline's own verdicts")
    parser.add_argument("--in", dest="in_path", type=str, default="eval_set.csv", help="Path to the labeled CSV (default: eval_set.csv)")
    parser.add_argument("--by-bucket", action="store_true", help="Also break down metrics per stratum_bucket")
    args = parser.parse_args()
    main(args.in_path, args.by_bucket)
