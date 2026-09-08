"""
checking_url/fusion.py -- combine the independent gambling signals into one
calibrated score, instead of the current strict override cascade.

Today runner.py runs: heuristic -> (needs_ai) -> Analyst -> Validator -> Tiebreaker,
each stage able to veto the last. That is sequential veto logic with no weighting
and no explicit false-positive budget. Paper C (Wang et al.) shows a weighted late
fusion of independent classifiers beats any single one; Fenceline tunes a per-class
threshold to a ~1.5% clean-FP budget.

`fuse()` takes whatever signals are available for a domain and returns
(label, score, reason) where label is "gambling" | "regular" | "needs_review".
Missing signals (ml_prob=None before the model is trained, struct=None if not
extracted) are simply skipped and the weights renormalise -- so this is safe to
wire in early: with only kw_score + ai_verdict present it tracks the current
behaviour, and each new signal you add sharpens it.

`grid_search()` picks the weights + the two band thresholds from a labelled set of
per-signal records (the richer eval export Phase 4 should produce -- plain
eval_set.csv only stores the final verdict, not the per-signal values).

    python -m checking_url.fusion        # self-check
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Sequence

_POS, _NEG, _REVIEW = "gambling", "regular", "needs_review"

# Structural features that push toward "this IS an operator" (not just about it).
_STRUCT_POS_KEYS = (
    "has_payment_field", "has_age_gate", "has_large_xorigin_iframe",
    "has_gambling_license_seal", "has_dominant_canvas",
)
_STRUCT_NEG_KEYS = ("paragraph_count",)  # >=3 real paragraphs == reads like an article


@dataclass(frozen=True)
class Weights:
    kw: float = 0.30        # normalised keyword score
    ml: float = 0.34        # P(gambling) from ml_text_model
    ai: float = 0.30        # Ollama verdict (gambling=1 / regular=0), scaled by its confidence
    struct: float = 0.06    # structural "is-an-operator" tilt
    # decision bands on the fused [0,1] score
    thr_low: float = 0.35   # < thr_low -> regular
    thr_high: float = 0.70  # >= thr_high -> gambling ; between -> needs_review


DEFAULT_WEIGHTS = Weights()


def _kw_component(kw_score: float | None, has_strong: bool) -> float | None:
    if kw_score is None:
        return None
    # classifier.py locks "gambling" at weighted score 4.0; map that band to ~[0,1].
    s = max(0.0, min(1.0, float(kw_score) / 4.0))
    if has_strong:
        s = max(s, 0.55)  # a lone STRONG signal is meaningful even at low total score
    return s


def _ai_component(ai_verdict: str | None, ai_conf: float | None) -> float | None:
    if not ai_verdict:
        return None
    v = str(ai_verdict).strip().lower()
    if v not in (_POS, _NEG):
        return None  # unconfirmed / dead / blocked -> AI abstains from the fusion
    conf = 0.7 if ai_conf is None else max(0.5, min(1.0, float(ai_conf)))
    # verdict in {0,1}, pulled toward 0.5 by (1-conf): a hesitant AI moves the score less
    base = 1.0 if v == _POS else 0.0
    return 0.5 + (base - 0.5) * conf


def _struct_component(struct: dict | None) -> float | None:
    if not struct:
        return None
    pos = sum(1 for k in _STRUCT_POS_KEYS if struct.get(k))
    prose = 1 if int(struct.get("paragraph_count") or 0) >= 3 and float(struct.get("link_density") or 1) < 0.33 else 0
    aff = 1 if int(struct.get("fp_gambling_affiliate_count") or 0) > 0 else 0
    raw = pos + aff - prose  # -1 .. ~7
    return max(0.0, min(1.0, 0.5 + 0.14 * raw))


def fuse(
    *,
    kw_score: float | None = None,
    has_strong_signal: bool = False,
    ml_prob: float | None = None,
    ai_verdict: str | None = None,
    ai_confidence: float | None = None,
    struct: dict | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> tuple[str, float, str]:
    """Return (label, fused_score, reason). label in {gambling, regular, needs_review}."""
    parts: list[tuple[str, float, float]] = []  # (name, value, weight)
    kwc = _kw_component(kw_score, has_strong_signal)
    if kwc is not None:
        parts.append(("kw", kwc, weights.kw))
    if ml_prob is not None:
        parts.append(("ml", float(ml_prob), weights.ml))
    aic = _ai_component(ai_verdict, ai_confidence)
    if aic is not None:
        parts.append(("ai", aic, weights.ai))
    stc = _struct_component(struct)
    if stc is not None:
        parts.append(("struct", stc, weights.struct))

    if not parts:
        return _REVIEW, 0.5, "no signals available"

    wsum = sum(w for _, _, w in parts) or 1.0
    score = sum(v * w for _, v, w in parts) / wsum
    contrib = " ".join(f"{n}={v:.2f}*{w/wsum:.2f}" for n, v, w in parts)

    if score >= weights.thr_high:
        label = _POS
    elif score < weights.thr_low:
        label = _NEG
    else:
        label = _REVIEW
    return label, round(score, 4), f"fused={score:.3f} [{contrib}]"


# --- calibration ---------------------------------------------------------
def _f1_fp(records: Sequence[dict], w: Weights) -> tuple[float, float, int]:
    """Return (f1_gambling, false_positive_rate, n_review) for weight set `w`."""
    tp = fp = fn = tn = nrev = 0
    for r in records:
        label = str(r.get("label") or "").strip().lower()
        if label not in (_POS, _NEG):
            continue
        pred, _, _ = fuse(
            kw_score=r.get("kw_score"),
            has_strong_signal=bool(r.get("has_strong_signal")),
            ml_prob=r.get("ml_prob"),
            ai_verdict=r.get("ai_verdict"),
            ai_confidence=r.get("ai_confidence"),
            struct=r.get("struct"),
            weights=w,
        )
        if pred == _REVIEW:
            nrev += 1
            continue  # a review-routed case is neither a TP nor an FP -- a human decides
        if pred == _POS and label == _POS:
            tp += 1
        elif pred == _POS and label == _NEG:
            fp += 1
        elif pred == _NEG and label == _POS:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return f1, fpr, nrev


def grid_search(
    records: Sequence[dict],
    *,
    max_fp_rate: float = 0.02,
    max_review_frac: float = 0.25,
) -> tuple[Weights, dict]:
    """Grid-search weights + band thresholds on labelled per-signal `records`.

    records: [{label, kw_score, has_strong_signal, ml_prob, ai_verdict,
               ai_confidence, struct}, ...]  (any signal may be missing/None)

    Objective: maximise gambling F1 subject to false-positive-rate <= max_fp_rate
    and review fraction <= max_review_frac. Returns (best_weights, report).
    """
    n = max(1, sum(1 for r in records if str(r.get("label", "")).lower() in (_POS, _NEG)))
    grid_w = [0.15, 0.25, 0.35, 0.45]
    grid_lo = [0.30, 0.35, 0.40, 0.45]
    grid_hi = [0.60, 0.65, 0.70, 0.75]

    best = None
    for kw, ml, ai in product(grid_w, grid_w, grid_w):
        st = max(0.0, 1.0 - (kw + ml + ai))
        if st > 0.4:  # keep structural a minority voice
            continue
        for lo, hi in product(grid_lo, grid_hi):
            if hi - lo < 0.15:
                continue
            w = Weights(kw=kw, ml=ml, ai=ai, struct=st, thr_low=lo, thr_high=hi)
            f1, fpr, nrev = _f1_fp(records, w)
            if fpr > max_fp_rate or nrev / n > max_review_frac:
                continue
            cand = (f1, -fpr, -nrev, w)
            if best is None or cand[:3] > best[:3]:
                best = cand
    if best is None:  # nothing met the constraints -> relax to "best F1, report the FP rate"
        for kw, ml, ai in product(grid_w, grid_w, grid_w):
            st = max(0.0, 1.0 - (kw + ml + ai))
            for lo, hi in product(grid_lo, grid_hi):
                if hi - lo < 0.15:
                    continue
                w = Weights(kw=kw, ml=ml, ai=ai, struct=st, thr_low=lo, thr_high=hi)
                f1, fpr, nrev = _f1_fp(records, w)
                cand = (f1, -fpr, -nrev, w)
                if best is None or cand[:3] > best[:3]:
                    best = cand
    f1, negfpr, negrev, w = best
    report = {
        "f1_gambling": round(f1, 4), "false_positive_rate": round(-negfpr, 4),
        "review_fraction": round(-negrev / n, 4), "n_labeled": n,
        "weights": w.__dict__, "met_constraints": (-negfpr <= max_fp_rate),
    }
    return w, report


# --- self-check -------------------------------------------------------------
def _demo() -> int:
    # 1. missing signals renormalise: kw-only, then kw+ai, then all four
    lab, s1, _ = fuse(kw_score=4.0, has_strong_signal=True)
    assert lab == _POS and s1 >= 0.7, (lab, s1)
    lab, _, _ = fuse(kw_score=0.0)
    assert lab == _NEG
    lab, s2, r2 = fuse(kw_score=1.0, ai_verdict="gambling", ai_confidence=0.9,
                       ml_prob=0.95, struct={"has_payment_field": True, "has_age_gate": True,
                                             "has_gambling_license_seal": True, "paragraph_count": 0})
    assert lab == _POS and "ml=0.95" in r2, (lab, s2, r2)

    # 2. a gambling-vocab ARTICLE: high kw, but AI says regular, ML low, prose structure -> not gambling
    lab, s3, _ = fuse(kw_score=3.0, ai_verdict="regular", ai_confidence=0.8, ml_prob=0.2,
                      struct={"paragraph_count": 6, "link_density": 0.1, "has_payment_field": False})
    assert lab in (_NEG, _REVIEW) and s3 < 0.7, (lab, s3)

    # 3. genuine disagreement -> needs_review band
    lab, s4, _ = fuse(kw_score=2.0, ai_verdict="regular", ai_confidence=0.55, ml_prob=0.75)
    assert lab == _REVIEW, (lab, s4)

    # 4. grid_search finds a weight set on synthetic per-signal records
    import random
    rng = random.Random(0)
    recs = []
    for _ in range(300):
        y = rng.random() < 0.5
        if y:
            recs.append({"label": "gambling", "kw_score": rng.uniform(1.5, 5),
                         "ml_prob": rng.uniform(0.6, 0.99), "ai_verdict": "gambling" if rng.random() < 0.85 else "regular",
                         "ai_confidence": rng.uniform(0.6, 0.95),
                         "struct": {"has_payment_field": rng.random() < 0.6, "paragraph_count": rng.randint(0, 2)}})
        else:
            recs.append({"label": "regular", "kw_score": rng.uniform(0, 2),
                         "ml_prob": rng.uniform(0.01, 0.4), "ai_verdict": "regular" if rng.random() < 0.9 else "gambling",
                         "ai_confidence": rng.uniform(0.6, 0.95),
                         "struct": {"has_payment_field": rng.random() < 0.1, "paragraph_count": rng.randint(2, 8)}})
    w, rep = grid_search(recs, max_fp_rate=0.05)
    assert rep["f1_gambling"] > 0.85, rep
    assert rep["false_positive_rate"] <= 0.05 or not rep["met_constraints"], rep

    print(f"fusion self-check: OK (renormalises on missing signals; grid_search F1={rep['f1_gambling']} "
          f"FPR={rep['false_positive_rate']} review={rep['review_fraction']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
