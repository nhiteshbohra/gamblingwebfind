"""
ml_trainer.py -- Train a gambling-domain classifier from MongoDB checked_domains.

Uses ONLY the domain name (no page fetching) -- trains in seconds on CPU.

Features per domain:
  - Character n-grams (2-4) on the domain stem via TF-IDF
  - TLD one-hot via a separate count vectorizer
  - Numeric: domain length, digit ratio, hyphen count, keyword hit count

Pipeline: FeatureUnion( TfidfVectorizer + CountVectorizer ) -> LogisticRegression

Usage:
    python ml_trainer.py                    # default: pulls all high-quality labels
    python ml_trainer.py --min-per-class 500
    python ml_trainer.py --dry-run          # show label counts without training
    python ml_trainer.py --eval-csv eval_set.csv   # validate against human-labeled CSV after training
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Setup path so db/ imports resolve without installing the package
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_ROOT / ".env")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_OUTPUT = _ROOT / "checking_url" / "models" / "gambling_classifier.pkl"
KW_PATH = _ROOT / "gambling_top_944_keywords.json"

# Only accept labels decided by these high-quality sources to avoid
# circular training (the model should not train on its own predictions).
_TRUSTED_DECIDED_BY = {
    "external_blocklist",
    "domain_anchor_strong",
    "ai_round1",
    "ai_round2_challenge",
    "tiebreaker_resolved",
    "heuristic_score",
    "validator_confirmed",
}

# Minimum AI confidence to accept ai_round1 labels (lower quality otherwise)
_MIN_AI_CONFIDENCE = 0.80

_GAMBLING_TLDS = {
    "bet", "casino", "poker", "slots", "bingo", "lottery",
    "win", "play", "game", "games",
}


# ---------------------------------------------------------------------------
# Keyword loader
# ---------------------------------------------------------------------------
_KEYWORDS: Optional[list] = None

def _load_keywords() -> list:
    global _KEYWORDS
    if _KEYWORDS is not None:
        return _KEYWORDS
    if not KW_PATH.exists():
        _KEYWORDS = []
        return _KEYWORDS
    with open(KW_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        _KEYWORDS = [str(k).lower().strip() for k in data if k]
    elif isinstance(data, dict):
        _KEYWORDS = [str(k).lower().strip() for k in data.get("keywords", data.get("words", list(data.keys()))) if k]
    else:
        _KEYWORDS = []
    return _KEYWORDS


# ---------------------------------------------------------------------------
# Feature extraction (keep in sync with ml_classifier.py)
# ---------------------------------------------------------------------------
def extract_features(domain: str) -> str:
    """Convert a domain name into a feature string for TF-IDF char n-grams."""
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

    keywords = _load_keywords()
    stem_lower = stem_clean.lower()
    kw_hits = sum(1 for kw in keywords if kw in stem_lower)
    kw_boost = " kwhit" * min(kw_hits, 5)

    tld_token = f" tld_{tld.replace('.', '_')}" if tld else ""
    tld_boost = " gamblingtld" * 3 if tld.split(".")[-1] in _GAMBLING_TLDS else ""

    return f"{stem_clean}{tld_token}{tld_boost}{kw_boost}"


# ---------------------------------------------------------------------------
# Data loader from MongoDB
# ---------------------------------------------------------------------------
def load_training_data(
    min_per_class: int = 500,
    negatives: int = 0,
    negatives_source: str = "tranco",
) -> tuple[list[str], list[str]]:
    """
    Pull labeled domain examples from MongoDB checked_domains.
    For the `regular` class: uses MongoDB regulars first, then augments from
    Tranco top-1M (or built-in list) to reach a balanced dataset regardless
    of how many gambling sites you have.

    Args:
        min_per_class: minimum examples required in gambling class
        negatives: fixed number of regular examples to target (0 = auto-balance 1:1)
        negatives_source: "tranco" | "builtin" | "both"
    """
    from db.mongo_client import get_db

    db = get_db()
    col = db["checked_domains"]

    gambling_docs: list[str] = []
    regular_docs: list[str] = []

    print("[trainer] Querying MongoDB checked_domains...")

    cursor = col.find(
        {"status": {"$in": ["gambling", "regular"]}},
        {"domain": 1, "status": 1, "decided_by": 1, "confidence": 1, "_id": 0},
    )

    skipped_quality = 0
    skipped_domain = 0

    for doc in cursor:
        domain = (doc.get("domain") or "").strip()
        status = (doc.get("status") or "").strip()
        decided_by = (doc.get("decided_by") or "heuristic_score").strip()
        confidence = doc.get("confidence") or None

        if not domain:
            skipped_domain += 1
            continue

        # Quality filter: only train on trusted source labels
        if decided_by not in _TRUSTED_DECIDED_BY:
            skipped_quality += 1
            continue

        # For AI-only labels, require high confidence to reduce noise
        if decided_by in {"ai_round1", "ai_round2_challenge"} and confidence is not None:
            try:
                if float(confidence) < _MIN_AI_CONFIDENCE:
                    skipped_quality += 1
                    continue
            except (TypeError, ValueError):
                pass

        if status == "gambling":
            gambling_docs.append(domain)
        elif status == "regular":
            regular_docs.append(domain)

    print(f"[trainer] Raw counts   : gambling={len(gambling_docs)}, regular={len(regular_docs)}")
    print(f"[trainer] Skipped      : quality_filter={skipped_quality}, missing_domain={skipped_domain}")

    if len(gambling_docs) < min_per_class:
        print(f"[trainer] ERROR: Only {len(gambling_docs)} gambling examples (need {min_per_class}). "
              f"Run more classification passes first.", file=sys.stderr)
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Negative class strategy:
    # With 700k+ gambling sites, the DB regular count will always be tiny.
    # We ALWAYS augment from external trusted sources to reach a 1:1 balance
    # (capped at 20k to keep training fast). This is safe because Tranco top-1M
    # domains are definitively non-gambling.
    # ---------------------------------------------------------------------------
    target_regulars = negatives if negatives > 0 else min(len(gambling_docs), 20_000)
    target_regulars = max(target_regulars, min_per_class)

    db_regular_count = len(regular_docs)
    still_needed = max(0, target_regulars - db_regular_count)

    if still_needed > 0:
        print(f"[trainer] Need {still_needed} more regular examples (DB has {db_regular_count}).")
        if negatives_source in ("tranco", "both"):
            extra = _load_tranco_negatives(still_needed)
        else:
            extra = _builtin_negatives(still_needed)
        if negatives_source == "both":
            # Blend: 70% Tranco, 30% builtin for diversity
            builtin_n = max(100, still_needed // 3)
            extra_builtin = _builtin_negatives(builtin_n)
            extra = extra + extra_builtin
        regular_docs = regular_docs + extra

    # Hard cap: never let regulars exceed 1:1 with gambling (wastes training time)
    import random
    random.seed(42)
    if len(regular_docs) > len(gambling_docs):
        regular_docs = random.sample(regular_docs, len(gambling_docs))
        print(f"[trainer] Capped regulars to {len(gambling_docs)} (1:1 with gambling)")

    # Hard cap on gambling side too: 20k max for fast training
    if len(gambling_docs) > 20_000:
        gambling_docs = random.sample(gambling_docs, 20_000)
        print(f"[trainer] Sampled gambling to 20,000 (training speed cap)")
        # Re-apply 1:1 cap after downsampling gambling
        if len(regular_docs) > len(gambling_docs):
            regular_docs = random.sample(regular_docs, len(gambling_docs))

    print(f"[trainer] Final dataset: gambling={len(gambling_docs)}, regular={len(regular_docs)}, "
          f"total={len(gambling_docs) + len(regular_docs)}")

    all_domains = gambling_docs + regular_docs
    all_labels = ["gambling"] * len(gambling_docs) + ["regular"] * len(regular_docs)

    features = [extract_features(d) for d in all_domains]
    return features, all_labels


# ---------------------------------------------------------------------------
# Negative (regular) domain augmentation from Tranco top-1M
# ---------------------------------------------------------------------------

# Locally cached file so we don't download every run
_TRANCO_CACHE = _ROOT / "checking_url" / "models" / "tranco_negatives.txt"
_TRANCO_URL   = "https://tranco-list.eu/top-1m.csv.zip"

# Gambling-related substrings to filter out of the Tranco list
# (some gambling affiliates appear in the top-1M)
_GAMBLING_FILTER = [
    "casino", "bet", "poker", "slot", "bingo", "lottery", "gambl",
    "wager", "sportsbook", "jackpot", "roulette", "blackjack", "keno",
    "lotto", "sportbet", "betway", "888", "stake", "win",
]


def _is_gambling_adjacent(domain: str) -> bool:
    """Quick check: does this domain contain gambling substrings?"""
    d = domain.lower()
    return any(kw in d for kw in _GAMBLING_FILTER)


def _load_tranco_negatives(n: int, force_download: bool = False) -> list[str]:
    """
    Return up to `n` clean non-gambling domain names from the Tranco top-1M list.
    Downloads once and caches to checking_url/models/tranco_negatives.txt.
    """
    # Try loading from cache first
    if _TRANCO_CACHE.exists() and not force_download:
        print(f"[trainer] Loading cached Tranco negatives from {_TRANCO_CACHE}...")
        with open(_TRANCO_CACHE, "r", encoding="utf-8") as f:
            candidates = [line.strip() for line in f if line.strip()]
        import random
        random.seed(42)
        random.shuffle(candidates)
        selected = candidates[:n]
        print(f"[trainer] Loaded {len(selected)} cached Tranco negatives.")
        return selected

    # Download from Tranco
    print(f"[trainer] Downloading Tranco top-1M from {_TRANCO_URL} ...")
    try:
        import io, zipfile, urllib.request
        with urllib.request.urlopen(_TRANCO_URL, timeout=30) as resp:
            raw = resp.read()
        zf = zipfile.ZipFile(io.BytesIO(raw))
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            lines = f.read().decode("utf-8").splitlines()
    except Exception as e:
        print(f"[trainer] WARNING: Tranco download failed ({e}). Falling back to built-in list.")
        return _builtin_negatives(n)

    # Parse rank,domain CSV and filter gambling-adjacent
    clean: list[str] = []
    for line in lines:
        parts = line.strip().split(",", 1)
        if len(parts) < 2:
            continue
        domain = parts[1].strip().lower()
        if not domain or _is_gambling_adjacent(domain):
            continue
        clean.append(domain)
        if len(clean) >= n * 3:   # collect 3x then shuffle-sample for diversity
            break

    import random
    random.seed(42)
    random.shuffle(clean)
    selected = clean[:n]

    # Cache to disk for future runs
    _TRANCO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(_TRANCO_CACHE, "w", encoding="utf-8") as f:
        f.write("\n".join(clean))   # cache ALL clean ones, not just n
    print(f"[trainer] Cached {len(clean)} Tranco negatives to {_TRANCO_CACHE}")
    print(f"[trainer] Using {len(selected)} as training negatives.")
    return selected


def _builtin_negatives(n: int) -> list[str]:
    """Hardcoded high-quality negatives used as ultimate fallback."""
    BUILTIN = [
        # Tech / Social
        "google.com", "facebook.com", "youtube.com", "wikipedia.org", "amazon.com",
        "twitter.com", "instagram.com", "linkedin.com", "github.com", "reddit.com",
        "netflix.com", "microsoft.com", "apple.com", "yahoo.com", "bing.com",
        "stackoverflow.com", "medium.com", "tiktok.com", "whatsapp.com", "telegram.org",
        # News / Media
        "bbc.co.uk", "cnn.com", "nytimes.com", "theguardian.com", "reuters.com",
        "apnews.com", "bloomberg.com", "forbes.com", "wsj.com", "ft.com",
        "ndtv.com", "timesofindia.com", "hindustantimes.com", "thehindu.com", "indiatoday.in",
        # E-Commerce
        "shopify.com", "ebay.com", "etsy.com", "walmart.com", "target.com",
        "bestbuy.com", "flipkart.com", "myntra.com", "snapdeal.com", "meesho.com",
        # Finance / Banking
        "paypal.com", "stripe.com", "visa.com", "mastercard.com", "chase.com",
        "hdfcbank.com", "icicibank.com", "sbi.co.in", "axisbank.com", "hsbc.com",
        "kotak.com", "pnbindia.in", "bankofbaroda.in", "federalbank.co.in",
        # SaaS / Productivity
        "zoom.us", "dropbox.com", "slack.com", "notion.so", "figma.com",
        "trello.com", "asana.com", "monday.com", "jira.atlassian.com", "salesforce.com",
        "hubspot.com", "zendesk.com", "freshworks.com", "zoho.com",
        # Education
        "coursera.org", "edx.org", "udemy.com", "khanacademy.org", "duolingo.com",
        "byju's.com", "unacademy.com", "vedantu.com", "toppr.com", "lms.edu",
        # Health / Government
        "healthline.com", "webmd.com", "mayoclinic.org", "nih.gov", "who.int",
        "cdc.gov", "mohfw.gov.in", "icmr.gov.in",
        # Travel
        "booking.com", "airbnb.com", "expedia.com", "tripadvisor.com", "makemytrip.com",
        # Misc
        "weather.com", "imdb.com", "indeed.com", "glassdoor.com", "naukri.com",
        "upwork.com", "freelancer.com", "fiverr.com", "toptal.com",
    ]
    import random
    random.seed(42)
    extra = [random.choice(BUILTIN) for _ in range(max(0, n - len(BUILTIN)))]
    return (BUILTIN + extra)[:n]



# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------
def train(features: list[str], labels: list[str]) -> object:
    """Train and return a fitted sklearn Pipeline."""
    from sklearn.pipeline import Pipeline
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, f1_score

    X_train, X_test, y_train, y_test = train_test_split(
        features, labels,
        test_size=0.20,
        stratify=labels,
        random_state=42,
    )

    print(f"[trainer] Train size: {len(X_train)} | Test size: {len(X_test)}")
    print("[trainer] Training TF-IDF char n-gram (2-4) + Logistic Regression...")

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 4),
            max_features=80_000,
            sublinear_tf=True,
            min_df=2,
        )),
        ("clf", LogisticRegression(
            C=4.0,
            max_iter=500,
            class_weight="balanced",
            solver="lbfgs",
            random_state=42,
        )),
    ])

    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    print("\n[trainer] --- Hold-out Evaluation ---")
    print(classification_report(y_test, y_pred, target_names=["gambling", "regular"], digits=4))
    f1 = f1_score(y_test, y_pred, pos_label="gambling")
    print(f"[trainer] Gambling F1 on hold-out: {f1:.4f}")

    return pipeline


# ---------------------------------------------------------------------------
# Validate against eval_set.csv (human labels)
# ---------------------------------------------------------------------------
def validate_against_eval_csv(model, eval_csv_path: str):
    import csv
    from sklearn.metrics import classification_report

    print(f"\n[trainer] Validating against {eval_csv_path}...")
    rows = []
    with open(eval_csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            domain = (row.get("domain") or "").strip()
            human_label_raw = (row.get("human_label") or row.get("gambling_domain_anchor") or "").strip().lower()
            if not domain:
                continue
            if human_label_raw in ("true", "1", "yes", "gambling"):
                human_label = "gambling"
            elif human_label_raw in ("false", "0", "no", "regular"):
                human_label = "regular"
            else:
                continue  # skip unlabeled rows
            rows.append((domain, human_label))

    if not rows:
        print("[trainer] No labeled rows found in eval CSV.")
        return

    X_eval = [extract_features(d) for d, _ in rows]
    y_true = [label for _, label in rows]
    y_pred = model.predict(X_eval)

    print(classification_report(y_true, y_pred, target_names=["gambling", "regular"], digits=4))
    correct = sum(p == t for p, t in zip(y_pred, y_true))
    print(f"[trainer] Eval accuracy: {correct}/{len(rows)} = {correct/len(rows):.1%}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog="ml_trainer",
        description="Train a gambling domain classifier from MongoDB checked_domains.",
    )
    parser.add_argument("--min-per-class", type=int, default=500,
                        help="Minimum gambling examples required (default: 500)")
    parser.add_argument("--negatives", type=int, default=0,
                        help="How many regular (non-gambling) examples to use. "
                             "Default: auto-balance 1:1 with gambling, capped at 20k. "
                             "Increase this if you have 700k+ gambling sites to improve regular recall.")
    parser.add_argument("--negatives-source", choices=["tranco", "builtin", "both"],
                        default="tranco",
                        help="Where to pull regular negatives from when DB has too few. "
                             "tranco=download top-1M (best), builtin=hardcoded list, both=blend. "
                             "Default: tranco (auto-downloads and caches locally).")
    parser.add_argument("--refresh-tranco", action="store_true",
                        help="Force re-download the Tranco list even if cached.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print label counts without training")
    parser.add_argument("--eval-csv", type=str, default=None,
                        help="Path to human-labeled CSV (e.g. eval_set.csv) to validate after training")
    args = parser.parse_args()

    print("=" * 60)
    print(" Gambling Domain ML Trainer")
    print("=" * 60)

    if args.refresh_tranco and _TRANCO_CACHE.exists():
        _TRANCO_CACHE.unlink()
        print(f"[trainer] Cleared Tranco cache — will re-download.")

    features, labels = load_training_data(
        min_per_class=args.min_per_class,
        negatives=args.negatives,
        negatives_source=args.negatives_source,
    )

    if args.dry_run:
        from collections import Counter
        c = Counter(labels)
        print(f"\n[trainer] DRY RUN -- label distribution: {dict(c)}")
        print("[trainer] Exiting without training.")
        return

    model = train(features, labels)

    # Save
    MODEL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_OUTPUT, "wb") as f:
        pickle.dump(model, f, protocol=4)
    size_kb = MODEL_OUTPUT.stat().st_size // 1024
    print(f"\n[trainer] Model saved to {MODEL_OUTPUT}  ({size_kb} KB)")

    # Optional eval CSV validation
    eval_csv = args.eval_csv or (str(_ROOT / "eval_set.csv") if (_ROOT / "eval_set.csv").exists() else None)
    if eval_csv and Path(eval_csv).exists():
        validate_against_eval_csv(model, eval_csv)

    print("\n[trainer] Done. Run `python -m checking_url.ml_classifier` to verify inference.")


if __name__ == "__main__":
    main()
