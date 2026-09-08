"""
checking_url/dedup.py -- 64-bit SimHash + Hamming near-duplicate collapse.

Why this exists
---------------
One gambling operator spawns dozens-to-hundreds of near-identical mirror domains
(same template, same copy, swapped brand token). Left uncollapsed they:
  * inflate every corpus count ("17.8k gambling" may be a few thousand distinct sites),
  * inflate any eval number (one operator's mirrors landing in the scored set makes
    the classifier look better/worse than it is),
  * bloat compliance reports ("1,000 links" that are 300 copies of one site).

This is the text-side analogue of a perceptual image hash: SimHash over the
title+meta+visible-text token bag, then collapse anything within `max_distance`
Hamming bits of a kept record. Ported from the approach in Babyhamsta/Fenceline's
classifier/dedup.py (see github-projects-comparison-report.md, row #3).

Pure functions, stdlib only. Not wired into the pipeline -- call it from
build_eval_set / score_eval_set / a corpus-audit script. See INTEGRATION.md.

    python -m checking_url.dedup        # runs the self-check
"""
from __future__ import annotations

import re
from typing import Callable, Sequence, TypeVar

_BITS = 64
_MASK = (1 << _BITS) - 1
_WORD_RE = re.compile(r"[a-z0-9]{2,}")

T = TypeVar("T")


def _fnv1a_64(s: str) -> int:
    """FNV-1a 64-bit over UTF-8 bytes. Fast, no deps, good enough dispersion for SimHash."""
    h = 0xCBF29CE484222325
    for b in s.encode("utf-8", "ignore"):
        h ^= b
        h = (h * 0x100000001B3) & _MASK
    return h


def simhash(text: str) -> int:
    """64-bit SimHash of a token bag.

    Token = lowercased [a-z0-9]{2,} run. Each token votes +1/-1 on every bit of its
    FNV-1a-64 hash; the sign of each column's tally becomes that output bit. Two
    documents that share most of their token mass end up within a few Hamming bits.
    """
    if not text:
        return 0
    v = [0] * _BITS
    n = 0
    for tok in _WORD_RE.findall(text.lower()):
        h = _fnv1a_64(tok)
        n += 1
        for b in range(_BITS):
            v[b] += 1 if (h >> b) & 1 else -1
    if n == 0:
        return 0
    out = 0
    for b in range(_BITS):
        if v[b] > 0:
            out |= 1 << b
    return out


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two 64-bit SimHashes."""
    return ((a ^ b) & _MASK).bit_count()


def doc_text(rec: dict) -> str:
    """Canonical string a record is hashed on: title + meta + visible text.

    Hashing the full doc (not body alone) keeps thin-but-distinct pages -- an
    interior game page can have a near-empty body under a loud <title> +
    og:description -- from all collapsing onto simhash("").
    """
    return " ".join(
        str(rec.get(k, "") or "") for k in ("title", "meta", "text", "body_excerpt")
    ).strip()


def collapse(
    records: Sequence[T],
    *,
    key: Callable[[T], str] = doc_text,  # type: ignore[assignment]
    max_distance: int = 4,
    min_tokens: int = 8,
) -> tuple[list[T], list[tuple[int, int]]]:
    """Greedy near-duplicate collapse.

    Returns (kept, dropped_pairs) where dropped_pairs is a list of
    (dropped_index, matched_kept_index) into the ORIGINAL `records` sequence, so a
    caller can log/inspect exactly what merged into what.

    - First occurrence of a cluster is kept; later near-duplicates are dropped.
    - Records with fewer than `min_tokens` word tokens are always kept (too little
      signal to hash reliably -- collapsing them risks merging unrelated thin pages).
    - O(n * kept) comparisons. Fine for tens of thousands; for millions, band the
      hashes first (not needed at this project's scale).
    """
    kept: list[T] = []
    kept_hashes: list[int] = []
    kept_orig_idx: list[int] = []
    dropped: list[tuple[int, int]] = []

    for i, rec in enumerate(records):
        text = key(rec)
        if len(_WORD_RE.findall(text.lower())) < min_tokens:
            kept.append(rec)
            kept_hashes.append(-1)  # sentinel: never matches (hamming with -1 is large)
            kept_orig_idx.append(i)
            continue
        h = simhash(text)
        hit = None
        for j, kh in enumerate(kept_hashes):
            if kh >= 0 and hamming(h, kh) <= max_distance:
                hit = j
                break
        if hit is None:
            kept.append(rec)
            kept_hashes.append(h)
            kept_orig_idx.append(i)
        else:
            dropped.append((i, kept_orig_idx[hit]))
    return kept, dropped


def dedup(records: Sequence[T], *, max_distance: int = 4, **kw) -> list[T]:
    """Convenience: just the kept records."""
    return collapse(records, max_distance=max_distance, **kw)[0]


# --- self-check -------------------------------------------------------------
def _demo() -> int:
    # A real operator mirror shares its ENTIRE template (nav, footer, T&C, game list,
    # provider names) across every domain and only swaps the brand token. Model that:
    # a long shared block + a per-mirror brand name.
    tmpl = (
        "Home Sports Casino Live Casino Slots Aviator Crash Rummy Poker Promotions VIP Help "
        "welcome bonus first deposit refer and earn cashback on losses minimum deposit "
        "instant withdrawal fast payout 24x7 support whatsapp telegram game providers "
        "pragmatic play evolution gaming jili spribe kingmaker responsible gambling 18 plus "
        "terms and conditions apply privacy policy about us contact daily jackpot weekly "
        "lucky draw tournament leaderboard live dealer baccarat dragon tiger andar bahar "
        "teen patti color prediction wingo daman lottery satta results "
    ) * 6
    m1 = {"title": "BrandAlpha Casino", "text": tmpl + " join BrandAlpha now trusted platform"}
    m2 = {"title": "BrandBeta Bet", "text": tmpl + " join BrandBeta now trusted platform"}
    m3 = {"title": "BrandGamma Play", "text": tmpl + " join BrandGamma now trusted platform"}
    unrelated = {
        "title": "Springfield Public Library",
        "text": (
            "catalog membership renewal hours events childrens reading room study spaces "
            "interlibrary loan reference desk community programs digital archives genealogy "
            "local history teen zone story time author talks book club printing scanning "
        ) * 6,
    }
    recs = [m1, unrelated, m2, m3]
    kept, dropped = collapse(recs, max_distance=4)

    assert len(kept) == 2, f"expected 2 distinct, got {len(kept)}: {[r['title'] for r in kept]}"
    assert {r["title"] for r in kept} == {"BrandAlpha Casino", "Springfield Public Library"}
    assert sorted(dropped) == [(2, 0), (3, 0)], f"unexpected drop map: {dropped}"

    # hamming sanity
    assert hamming(0, 0) == 0
    assert hamming(0b1011, 0b0001) == 2
    # an unrelated page must stay far away (not merged)
    assert hamming(simhash(doc_text(m1)), simhash(doc_text(unrelated))) > 15
    # tiny docs are never collapsed (too little signal to hash)
    tiny = [{"text": "casino"}, {"text": "casino"}]
    assert len(dedup(tiny)) == 2, "sub-min_tokens docs must not collapse"

    print("dedup self-check: OK (4 records -> 2 distinct, 2 brand-swap mirrors dropped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
