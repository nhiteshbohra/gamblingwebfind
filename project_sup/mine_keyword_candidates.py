r"""
project_sup/mine_keyword_candidates.py
=========================================
Mines the existing ~17k "gambling" + ~600 "regular" screenshot corpus for candidate
keywords NOT already in checking_url/classifier.py's signal lists.

NOT a classifier and NOT training data -- this only surfaces words that show up in a
disproportionate share of gambling screenshots vs. regular ones, for a HUMAN to look at
and decide whether to add to STRONG_GAMBLING_SIGNALS / WEAK_GAMBLING_SIGNALS. Training a
model on these same self-labeled statuses would just teach it to reproduce this
pipeline's own mistakes (see build_eval_set.py's non-circularity notes) -- mining
vocabulary and adding a human-reviewed rule is the safe way to use this corpus.

Uses document frequency (how many DISTINCT screenshots contain a word), not raw word
count, so one page repeating a banner 50 times can't dominate the ranking.

Only reads screenshots already on disk (checking_url/ocr_extractor.py, RapidOCR) --
no network fetch, no Ollama. Excludes circular label sources (external_blocklist,
known_gambling_runner) same as build_eval_set.py.

Usage:
    python -m project_sup.mine_keyword_candidates
    python -m project_sup.mine_keyword_candidates --sample 1500 --min-doc-freq 5 --top 40
"""
import argparse
import random
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import get_db, checked_domains
from export_domains.screenshot import find_screenshot_path
from checking_url.ocr_extractor import extract_ocr_text
from checking_url.classifier import (
    STRONG_GAMBLING_SIGNALS, WEAK_GAMBLING_SIGNALS, BARE_CATEGORY_SIGNALS,
)

_KNOWN_SIGNALS = STRONG_GAMBLING_SIGNALS | WEAK_GAMBLING_SIGNALS | BARE_CATEGORY_SIGNALS

# ponytail: tiny built-in stopword list, not a library -- good enough to strip nav/legal
# boilerplate ("click here", "privacy policy") that would otherwise dominate every page.
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "your", "you", "are", "was",
    "will", "has", "have", "not", "all", "our", "can", "more", "click", "here", "home",
    "about", "page", "contact", "privacy", "policy", "terms", "copyright", "rights",
    "reserved", "menu", "search", "login", "sign", "welcome", "please", "view", "read",
    "new", "now", "get", "use", "how", "who", "why", "what", "when", "than", "then",
    "into", "out", "over", "under", "any", "may", "must", "www", "com", "http", "https",
}
_WORD_RE = re.compile(r"[a-zA-Z]{3,}")

# Same circularity exclusion as checking_url/build_eval_set.py -- these labels never
# went through classify()/classify_with_challenge(), so mining them isn't measuring our
# own classifier's vocabulary at all.
_CIRCULAR_REASON_EXACT = "Known gambling — confirmed true positive"


def _is_circular(doc: dict) -> bool:
    if doc.get("decided_by") == "external_blocklist":
        return True
    return str(doc.get("reason", "")).strip() == _CIRCULAR_REASON_EXACT


def _doc_frequencies(domains: list[dict], label: str, sample: int, rng: random.Random) -> tuple[Counter, int]:
    """Returns (doc_freq counter, n_screenshots_actually_read)."""
    candidates = [d for d in domains if not _is_circular(d)]
    chosen = rng.sample(candidates, min(sample, len(candidates)))
    doc_freq = Counter()
    n_read = 0
    for d in chosen:
        domain = d.get("_id") or d.get("domain")
        url = d.get("url") or f"https://{domain}"
        path = find_screenshot_path(url, domain)
        if not path:
            continue
        text = extract_ocr_text(path)
        if not text:
            continue
        n_read += 1
        words = {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS
        doc_freq.update(words)
    print(f"  [{label}] read {n_read}/{len(chosen)} screenshots ({len(candidates)} eligible candidates)")
    return doc_freq, n_read


def mine(sample: int, min_doc_freq: int, top: int, seed: int | None) -> list[tuple[str, int, int, float]]:
    get_db()
    rng = random.Random(seed)

    gambling_docs = list(checked_domains().find({"status": "gambling"}, {"_id": 1, "domain": 1, "url": 1, "decided_by": 1, "reason": 1}))
    regular_docs = list(checked_domains().find({"status": "regular"}, {"_id": 1, "domain": 1, "url": 1, "decided_by": 1, "reason": 1}))
    print(f"Corpus available: {len(gambling_docs)} gambling, {len(regular_docs)} regular")

    print("Reading gambling screenshots...")
    g_freq, n_g = _doc_frequencies(gambling_docs, "gambling", sample, rng)
    print("Reading regular screenshots...")
    r_freq, n_r = _doc_frequencies(regular_docs, "regular", sample, rng)

    if n_g == 0:
        print("No gambling screenshots found on disk -- nothing to mine.")
        return []

    results = []
    for word, g_count in g_freq.items():
        if g_count < min_doc_freq or word in _KNOWN_SIGNALS:
            continue
        r_count = r_freq.get(word, 0)
        # +1 smoothing on the regular side: a word never seen in the (much smaller)
        # regular sample shouldn't score as "infinitely" gambling-specific.
        ratio = (g_count / n_g) / ((r_count + 1) / (n_r + 1))
        results.append((word, g_count, r_count, ratio))

    results.sort(key=lambda x: x[3], reverse=True)
    top_results = results[:top]

    print("\n" + "=" * 70)
    print(f"  TOP {len(top_results)} CANDIDATE KEYWORDS (not already in classifier.py's signal lists)")
    print("=" * 70)
    print(f"  {'word':<20} {'gambling docs':<15} {'regular docs':<14} {'ratio':<8}")
    print("  " + "-" * 60)
    for word, g_count, r_count, ratio in top_results:
        print(f"  {word:<20} {g_count:<15} {r_count:<14} {ratio:<8.1f}")
    print("=" * 70)
    print(
        "\nThese are candidates, not verdicts. Check a few real screenshots for each word,\n"
        "then hand-add the ones that are genuinely gambling-specific to STRONG_GAMBLING_SIGNALS\n"
        "or WEAK_GAMBLING_SIGNALS in checking_url/classifier.py -- same as the manual bug-hunting\n"
        "process that found ibinfra.in/pokerledger.net/casinobonuschecker.com this session, just\n"
        "surfaced automatically instead of by eye.\n"
    )
    return top_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mine candidate gambling keywords from the existing screenshot corpus")
    parser.add_argument("--sample", type=int, default=1000, help="Max screenshots to OCR per class (default 1000)")
    parser.add_argument("--min-doc-freq", type=int, default=5, help="Minimum gambling-screenshot occurrences to consider a word (default 5)")
    parser.add_argument("--top", type=int, default=40, help="How many top candidates to print (default 40)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sampling")
    args = parser.parse_args()
    mine(args.sample, args.min_doc_freq, args.top, args.seed)
