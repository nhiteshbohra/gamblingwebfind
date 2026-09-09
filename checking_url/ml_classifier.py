"""
checking_url/ml_classifier.py -- Thin sklearn inference wrapper.

Loads the pre-trained gambling domain classifier (trained by ml_trainer.py)
once as a lazy singleton and exposes two public functions:

    predict_proba(domain) -> float      # 0.0 = definitely regular, 1.0 = definitely gambling
    predict_label(domain) -> str        # "gambling" | "regular" | "uncertain"

Graceful fallback: returns 0.5 ("uncertain") if the model file does not yet exist,
so the pipeline runs unchanged before you train for the first time.

Model file location: checking_url/models/gambling_classifier.pkl
Train/refresh with:  python ml_trainer.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

_MODEL_PATH = Path(__file__).resolve().parent / "models" / "gambling_classifier.pkl"

_MODEL = None
_FEATURE_FN = None

_GAMBLING_KEYWORDS: Optional[list] = None

_GAMBLING_TLDS = {
    "bet", "casino", "poker", "slots", "bingo", "lottery",
    "win", "play", "game", "games",
}


def _load_keywords_once() -> list:
    global _GAMBLING_KEYWORDS
    if _GAMBLING_KEYWORDS is not None:
        return _GAMBLING_KEYWORDS
    try:
        import json
        kw_path = Path(__file__).resolve().parent.parent / "gambling_top_944_keywords.json"
        with open(kw_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _GAMBLING_KEYWORDS = [str(k).lower().strip() for k in data if k]
        elif isinstance(data, dict):
            _GAMBLING_KEYWORDS = [str(k).lower().strip() for k in data.get("keywords", data.get("words", list(data.keys()))) if k]
        else:
            _GAMBLING_KEYWORDS = []
    except Exception:
        _GAMBLING_KEYWORDS = []
    return _GAMBLING_KEYWORDS


def _default_feature_fn(domain: str) -> str:
    """Convert a domain name into a feature string for the TF-IDF vectorizer."""
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].split(":")[0].removeprefix("www.")

    try:
        import tldextract
        parts = tldextract.extract(domain)
        stem = (parts.subdomain + " " + parts.domain).strip() if parts.subdomain else parts.domain
        tld = parts.suffix or ""
    except Exception:
        dot_idx = domain.rfind(".")
        stem = domain[:dot_idx] if dot_idx > 0 else domain
        tld = domain[dot_idx + 1:] if dot_idx > 0 else ""

    stem_clean = re.sub(r"[-_.]", " ", stem)
    stem_clean = re.sub(r"\d+", " NUM ", stem_clean)

    keywords = _load_keywords_once()
    stem_lower = stem_clean.lower()
    kw_hits = sum(1 for kw in keywords if kw in stem_lower)
    kw_boost = " kwhit" * min(kw_hits, 5)

    tld_token = f" tld_{tld.replace('.', '_')}" if tld else ""
    tld_boost = " gamblingtld" * 3 if tld.split(".")[-1] in _GAMBLING_TLDS else ""

    return f"{stem_clean}{tld_token}{tld_boost}{kw_boost}"


def _load_model():
    global _MODEL, _FEATURE_FN
    if _MODEL is not None:
        return (_MODEL, _FEATURE_FN) if _MODEL is not False else (None, None)

    if not _MODEL_PATH.exists():
        _MODEL = False
        return None, None

    try:
        import pickle
        with open(_MODEL_PATH, "rb") as f:
            payload = pickle.load(f)
        if isinstance(payload, dict):
            _MODEL = payload["model"]
            _FEATURE_FN = payload.get("feature_fn", _default_feature_fn)
        else:
            _MODEL = payload
            _FEATURE_FN = _default_feature_fn
        return _MODEL, _FEATURE_FN
    except Exception as e:
        print(f"[ml_classifier] WARNING: Failed to load model from {_MODEL_PATH}: {e}", file=sys.stderr)
        _MODEL = False
        return None, None


def predict_proba(domain: str) -> float:
    """Return P(gambling) in [0.0, 1.0]. Returns 0.5 if model not loaded."""
    if not domain:
        return 0.5
    model, feature_fn = _load_model()
    if model is None:
        return 0.5
    try:
        feat = feature_fn(domain)
        proba = model.predict_proba([feat])[0]
        classes = list(model.classes_)
        gambling_idx = classes.index("gambling") if "gambling" in classes else 1
        return float(proba[gambling_idx])
    except Exception as e:
        print(f"[ml_classifier] predict_proba error for '{domain}': {e}", file=sys.stderr)
        return 0.5


def predict_label(domain: str,
                  gambling_thr: float = 0.75,
                  regular_thr: float = 0.20) -> str:
    """Return 'gambling', 'regular', or 'uncertain'."""
    p = predict_proba(domain)
    if p >= gambling_thr:
        return "gambling"
    if p <= regular_thr:
        return "regular"
    return "uncertain"


def is_model_loaded() -> bool:
    model, _ = _load_model()
    return model is not None


if __name__ == "__main__":
    test_cases = [
        ("royalcasino.bet", "gambling"),
        ("luckyspin777.online", "gambling"),
        ("sportsbetting24.com", "gambling"),
        ("netbanking.hdfc.com", "regular"),
        ("amazon.com", "regular"),
        ("news.ycombinator.com", "regular"),
    ]
    if not is_model_loaded():
        print("[ml_classifier] Model not trained yet -- run: python ml_trainer.py")
        print("[ml_classifier] Self-test skipped (returns 0.5 for all).")
    else:
        print("[ml_classifier] Model loaded. Running self-test...")
        for domain, expected in test_cases:
            p = predict_proba(domain)
            label = predict_label(domain)
            match = "OK" if label == expected else f"FAIL (expected {expected})"
            print(f"  {domain:<40} -> prob={p:.3f} label={label} {match}")
        print("[ml_classifier] Self-test done.")
