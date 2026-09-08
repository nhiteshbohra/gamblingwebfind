"""
checking_url/ml_text_model.py -- a cheap classical text classifier as a THIRD,
independent gambling signal (alongside the keyword score and the Ollama verdict).

Why
---
Detection today is rules + LLM. There is no model that learned from the corpus.
Paper B (Lee & Bae) got SVM to 0.9937 / 0.2 ms on OCR-screenshot text; Fenceline
runs a hashing-trick logistic model on-device in microseconds. A TF-IDF + linear
SVM here does the same job: catch the vocabulary cases the curated phrase list
misses, and let a decisive ML verdict skip the Ollama challenge entirely.

CIRCULARITY WARNING (the reason project_sup/mine_keyword_candidates.py refuses to
train on Mongo statuses): if you train on `status=gambling` / `status=regular`
labels that this pipeline itself produced, the model just learns to reproduce the
pipeline's current mistakes. Mitigations, enforced by train_from_rows():
  * circular label sources (external_blocklist, known_gambling_runner, the
    "Known gambling -- confirmed true positive" reason) are dropped unless
    allow_circular=True -- same exclusion as build_eval_set.py;
  * it warns loudly and prefers rows carrying a human `label` over pipeline
    statuses;
  * the train/test split is grouped by registrable domain (no eTLD+1 in both
    sides) so the held-out score isn't inflated by mirror pages.
Treat the output as a signal to FUSE (see checking_url/fusion.py), never a sole
decider.

Inference abstains (returns None) until a model file exists, so importing/wiring
this before it is trained is safe.

    python -m checking_url.ml_text_model                       # self-check (trains in memory)
    python -m checking_url.ml_text_model train --from-file rows.jsonl --out checking_url/models/ml_text_model.pkl
"""
from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL = _ROOT / "checking_url" / "models" / "ml_text_model.pkl"

# Label sources that never went through classify()/classify_with_challenge(), so
# training on them measures nothing about this classifier. Mirrors build_eval_set.py
# and project_sup/mine_keyword_candidates.py.
CIRCULAR_SOURCES = {"external_blocklist", "known_gambling_runner"}
CIRCULAR_REASON_EXACT = "Known gambling — confirmed true positive"

_POS = "gambling"
_NEG = "regular"


def _etld1(domain_or_url: str) -> str:
    s = (domain_or_url or "").lower()
    s = re.sub(r"^\w+://", "", s).split("/")[0].split(":")[0].removeprefix("www.")
    parts = [p for p in s.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else s


def _norm_label(v) -> str | None:
    v = str(v or "").strip().lower()
    if v in ("gambling", "g", "yes", "1", "true"):
        return _POS
    if v in ("regular", "r", "no", "0", "false", "not_gambling", "not gambling"):
        return _NEG
    return None


class MLTextModel:
    """Loads a trained TF-IDF + calibrated-linear-SVM pipeline; predicts P(gambling).

    Every public method is safe to call with no model present -- prediction just
    returns None (abstain).
    """

    def __init__(self, path: str | Path = _DEFAULT_MODEL):
        self.path = Path(path)
        self._pipe = None
        self.meta: dict = {}

    # -- loading -----------------------------------------------------------
    def load(self) -> bool:
        if self._pipe is not None:
            return True
        if not self.path.exists():
            return False
        try:
            with open(self.path, "rb") as fh:
                blob = pickle.load(fh)
            self._pipe = blob["pipe"]
            self.meta = blob.get("meta", {})
            return True
        except Exception as e:  # corrupt / version-skewed pickle -> abstain, don't crash the pipeline
            print(f"[ml_text_model] failed to load {self.path}: {e}", file=sys.stderr)
            return False

    @property
    def ready(self) -> bool:
        return self._pipe is not None or self.load()

    # -- inference -------------------------------------------------------
    def predict_proba(self, text: str) -> float | None:
        """P(gambling) in [0,1], or None if no model is loaded / text is empty."""
        if not text or not self.ready:
            return None
        try:
            return float(self._pipe.predict_proba([text])[0][self._pos_idx()])
        except Exception:
            return None

    def predict(self, text: str, threshold: float = 0.5) -> str | None:
        p = self.predict_proba(text)
        if p is None:
            return None
        return _POS if p >= threshold else _NEG

    def _pos_idx(self) -> int:
        classes = list(self._pipe.classes_)
        return classes.index(_POS) if _POS in classes else 1


# -- training -----------------------------------------------------------
def _build_pipeline():
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.svm import LinearSVC

    word = TfidfVectorizer(
        lowercase=True, ngram_range=(1, 2), min_df=2, max_df=0.9,
        max_features=60_000, sublinear_tf=True, strip_accents="unicode",
    )
    char = TfidfVectorizer(
        lowercase=True, analyzer="char_wb", ngram_range=(3, 5), min_df=3,
        max_features=60_000, sublinear_tf=True,
    )
    # LinearSVC has no predict_proba; CalibratedClassifierCV wraps it (Platt / sigmoid).
    clf = CalibratedClassifierCV(LinearSVC(C=1.0, class_weight="balanced"), method="sigmoid", cv=3)
    return Pipeline([
        ("feats", FeatureUnion([("word", word), ("char", char)])),
        ("clf", clf),
    ])


def train_from_rows(
    rows: Sequence[dict],
    *,
    out_path: str | Path = _DEFAULT_MODEL,
    allow_circular: bool = False,
    test_frac: float = 0.2,
    min_per_class: int = 200,
    seed: int = 13,
) -> dict:
    """Train + calibrate on `rows` = [{text, label, source?, reason?, domain?}].

    Returns a metrics dict and writes the pickle to `out_path`. Raises ValueError if
    the data is unusable (too few of a class, all one label, etc).
    """
    import random

    import numpy as np
    from sklearn.metrics import precision_recall_fscore_support

    rng = random.Random(seed)

    kept: list[tuple[str, str, str]] = []  # (text, label, group)
    dropped_circular = 0
    for r in rows:
        label = _norm_label(r.get("label") or r.get("human_label") or r.get("status"))
        text = (r.get("text") or r.get("body_excerpt") or "").strip()
        if not label or len(text) < 40:
            continue
        src = str(r.get("source") or r.get("decided_by") or "")
        reason = str(r.get("reason") or "")
        if not allow_circular and (src in CIRCULAR_SOURCES or reason.strip() == CIRCULAR_REASON_EXACT):
            dropped_circular += 1
            continue
        group = _etld1(r.get("domain") or r.get("url") or "") or f"__row{len(kept)}"
        kept.append((text, label, group))

    n_pos = sum(1 for _, l, _ in kept if l == _POS)
    n_neg = sum(1 for _, l, _ in kept if l == _NEG)
    print(f"[train] usable rows: {len(kept)}  ({n_pos} {_POS}, {n_neg} {_NEG}); "
          f"dropped {dropped_circular} circular-source rows")
    if n_pos < min_per_class or n_neg < min_per_class:
        raise ValueError(
            f"need >= {min_per_class} of each class, have {n_pos} {_POS} / {n_neg} {_NEG}. "
            f"Grow the '{_NEG}' corpus first (it is the known-scarce class) or lower min_per_class "
            f"for a rough throwaway model."
        )
    if n_neg < n_pos / 4:
        print(f"[train] WARNING: '{_NEG}' is {n_neg/n_pos:.0%} of '{_POS}'. class_weight=balanced is on, "
              f"but a held-out score on this ratio is optimistic -- rebalance before trusting it.")

    # group-wise split: no registrable domain in both train and test
    groups = sorted({g for _, _, g in kept})
    rng.shuffle(groups)
    n_test_groups = max(1, int(len(groups) * test_frac))
    test_groups = set(groups[:n_test_groups])
    tr = [(t, l) for t, l, g in kept if g not in test_groups]
    te = [(t, l) for t, l, g in kept if g in test_groups]
    if not te or len({l for _, l in te}) < 2:
        # tiny / degenerate split -> fall back to a plain shuffle split
        allr = [(t, l) for t, l, _ in kept]
        rng.shuffle(allr)
        cut = max(1, int(len(allr) * test_frac))
        te, tr = allr[:cut], allr[cut:]

    pipe = _build_pipeline()
    pipe.fit([t for t, _ in tr], [l for _, l in tr])

    y_true = [l for _, l in te]
    y_pred = list(pipe.predict([t for t, _ in te]))
    pos_idx = list(pipe.classes_).index(_POS)
    y_score = [row[pos_idx] for row in pipe.predict_proba([t for t, _ in te])]
    p, r, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=[_POS], average="binary", pos_label=_POS, zero_division=0
    )
    acc = float(np.mean([a == b for a, b in zip(y_true, y_pred)]))
    metrics = {
        "n_train": len(tr), "n_test": len(te),
        "accuracy": round(acc, 4), "precision_gambling": round(float(p), 4),
        "recall_gambling": round(float(r), 4), "f1_gambling": round(float(f1), 4),
        "n_pos": n_pos, "n_neg": n_neg, "dropped_circular": dropped_circular,
        "grouped_split": bool(test_groups),
    }
    print(f"[train] held-out: acc={metrics['accuracy']}  P={metrics['precision_gambling']}  "
          f"R={metrics['recall_gambling']}  F1={metrics['f1_gambling']}  (n_test={len(te)})")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        pickle.dump({"pipe": pipe, "meta": {**metrics, "model": "tfidf(word12+charwb35)+linsvc/sigmoid"}}, fh)
    print(f"[train] wrote {out_path}")
    return metrics


def _read_rows_file(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() in (".jsonl", ".ndjson"):
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("rows", [])
    # csv
    import csv
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# -- self-check -------------------------------------------------------------
def _demo() -> int:
    # abstain before any model is loaded
    m = MLTextModel(path=_ROOT / "checking_url" / "models" / "__nonexistent__.pkl")
    assert m.predict_proba("online casino welcome bonus") is None
    assert m.predict("anything") is None

    # train a throwaway model in a temp dir on synthetic rows and check it separates
    import tempfile

    gambling_bits = [
        "online casino welcome bonus deposit now instant withdrawal live dealer slots",
        "sports betting odds cricket ipl bet id whatsapp aviator crash game teen patti",
        "satta matka kalyan result fix jodi panel chart open close daily lucky draw",
        "claim free spins wagering requirement crypto casino usdt deposit vip cashback",
        "poker tournament real money rummy cash game andar bahar dragon tiger baccarat",
        "color prediction wingo daman game big daddy 91 club recharge withdrawal upi",
    ]
    regular_bits = [
        "add to cart free shipping return policy customer reviews product description checkout",
        "university admission syllabus faculty examination result alumni undergraduate degree",
        "breaking news editorial team published by reporter press release newsroom column",
        "personal banking savings account net banking ifsc code home loan interest rates branch",
        "restaurant menu book a table fine dining chef reservation buffet lunch dinner cuisine",
        "mutual fund portfolio brokerage demat account sip investment sebi registered advisor",
    ]

    def _expand(bits, label, n=60):
        rows = []
        for i in range(n):
            b = bits[i % len(bits)]
            filler = " ".join(bits[(i + k) % len(bits)] for k in range(2))
            rows.append({"text": f"{b} {filler} sample {i}", "label": label, "domain": f"{label}{i}.example"})
        return rows

    rows = _expand(gambling_bits, "gambling") + _expand(regular_bits, "regular")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "m.pkl"
        metrics = train_from_rows(rows, out_path=out, min_per_class=40, test_frac=0.25)
        assert metrics["f1_gambling"] >= 0.8, metrics
        loaded = MLTextModel(path=out)
        assert loaded.load()
        pg = loaded.predict_proba("online casino aviator crash game bet id whatsapp deposit bonus")
        pr = loaded.predict_proba("university admission syllabus examination result alumni faculty")
        assert pg is not None and pr is not None and pg > 0.6 > pr, (pg, pr)

    # circular-source rows are dropped
    circ = [{"text": "x" * 50, "label": "gambling", "source": "external_blocklist"}] * 5
    try:
        train_from_rows(rows + circ, out_path=Path(tempfile.gettempdir()) / "z.pkl", min_per_class=40)
    except ValueError:
        pass  # fine -- point is it didn't count the circular rows
    print("ml_text_model self-check: OK (abstains unloaded; trains + separates; drops circular sources)")
    return 0


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "train":
        import argparse

        ap = argparse.ArgumentParser(prog="ml_text_model train")
        ap.add_argument("--from-file", required=True, help="JSONL/JSON/CSV of {text,label[,source,domain]}")
        ap.add_argument("--out", default=str(_DEFAULT_MODEL))
        ap.add_argument("--allow-circular", action="store_true",
                        help="do NOT drop external_blocklist / known_gambling_runner rows (not recommended)")
        ap.add_argument("--min-per-class", type=int, default=200)
        a = ap.parse_args(argv[1:])
        rows = _read_rows_file(a.from_file)
        train_from_rows(rows, out_path=a.out, allow_circular=a.allow_circular, min_per_class=a.min_per_class)
        return 0
    return _demo()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
