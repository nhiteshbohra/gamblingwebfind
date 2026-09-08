"""
checking_url/structural_features.py -- numeric "what the page IS and DOES" vector.

Why this exists
---------------
classifier.py already has strong *rule* gates that separate an operator from a page
ABOUT gambling (looks_like_editorial, the affiliate/hospitality archetypes,
detect_igaming_providers, ...). What it does NOT have is those signals as a fixed
numeric vector a learned model can weigh against the keyword score and the AI
verdict. This module produces that vector -- ~30 cheap scalars over HTML + URL that
a downstream fusion model (checking_url/fusion.py) consumes. It changes no existing
decision on its own.

Adapted from Babyhamsta/Fenceline's extension/content/structural-features.js
(github-projects-comparison-report.md, row #6). Fenceline reads a live DOM; we only
have server-rendered HTML, so area-based features (canvas/iframe viewport fraction)
degrade to presence + count, and text is BeautifulSoup's get_text rather than
innerText. Everything is a pure function of (html, url).

Also exposes detect_glyph_cipher(): a page whose visible text is a long string
drawn from a tiny distinct-codepoint alphabet is running a font-substitution cipher
(the DOM text is gibberish the keyword scanner and the LLM can't read) -- route
those to OCR / the vision model instead of trusting the text. From Fenceline's
extension/lib/detect/glyph-cipher.js (row #8).

    python -m checking_url.structural_features        # runs the self-check
"""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

try:
    import tldextract

    def _registrable(host: str) -> str:
        ext = tldextract.extract(host or "")
        return ".".join(p for p in (ext.domain, ext.suffix) if p).lower()
except Exception:  # pragma: no cover - tldextract is a declared dep, this is belt-and-braces
    def _registrable(host: str) -> str:
        parts = (host or "").lower().split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


# Cheap / abused TLDs carry signal on gambling throwaway domains.
_CHEAP_TLDS = {
    "xyz", "top", "click", "club", "online", "site", "live", "fun", "gq", "cf",
    "ml", "tk", "ga", "buzz", "rest", "cyou", "icu", "vip", "bet", "casino", "win",
}
_GAMBLING_URL_TOKENS = (
    "casino", "bet", "slot", "poker", "gambl", "wager", "roulette", "blackjack",
    "satta", "matka", "rummy", "aviator", "jackpot", "lotto", "lottery", "teenpatti",
    "andarbahar", "baccarat", "sportsbook", "wingo", "daman", "1xbet", "melbet",
)

# Gambling-AFFILIATE / tracker / platform hosts -- distinct from the game-provider
# CDNs in ai_classifier.IGAMING_PROVIDERS. Substring-matched against script/iframe
# src hosts. Feature input only, never a block boundary on its own.
_FP_GAMBLING_AFFILIATE = (
    "income-access", "incomeaccess", "raventrack", "netrefer", "myaffiliates",
    "cellxpert", "smartico", "betradar", "sportradar", "everymatrix", "softswiss",
    "affilka", "myaffiliate", "trackier", "affise", "voluum", "scaleo",
)
_FP_CRYPTO_WIDGET = (
    "coinbase-commerce", "coingate", "nowpayments", "cryptomus", "coinpayments",
    "moonpay", "wert.io", "changelly", "web3modal", "walletconnect",
)

_AGE_GATE_RE = re.compile(
    r"age\s*(?:verification|gate|check)|must be (?:18|21|over)|adults? only|"
    r"18\s*\+|21\s*\+|are you (?:18|21)|confirm your age",
    re.I,
)
_LICENSE_SEAL_RE = re.compile(
    r"curacao|cura[cç]ao|gaming licen[cs]e|gambling commission|licen[cs]ed (?:and )?regulated|"
    r"mga/|malta gaming|begambleaware|gamcare|gamstop|kahnawake",
    re.I,
)
_PAYMENT_FIELD_RE = re.compile(
    r"card.?number|\bcvv\b|\bcvc\b|\bccnum|card.?holder|expir|\bcc-(?:number|csc|exp)\b", re.I
)
_POPUNDER_RE = re.compile(r"window\.open\s*\(|popunder|pop_under|\.popups?\b", re.I)
_URL_INPUT_RE = re.compile(r"\b(url|http|https|site|address|proxy|website|link)\b", re.I)
_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _norm_host(url: str) -> tuple[str, str, str, str]:
    parts = urlsplit(url if "://" in (url or "") else f"https://{url or ''}")
    return parts.hostname or "", parts.path or "/", parts.query or "", (url or "")


def structural_features(html: str, url: str = "") -> dict:
    """Return a fixed-key dict of numeric/boolean structural features.

    Every key is always present (0 / 0.0 / False default) so the vector is
    fixed-length and safe to store verbatim or hand to sklearn. Never raises on
    junk input -- a wedged page yields a clean zero-ish vector, not an exception.
    """
    host, path, query, raw = _norm_host(url)
    reg = _registrable(host)
    host_labels = [x for x in host.split(".") if x]
    tld = host_labels[-1] if host_labels else ""
    raw_l = (raw or "").lower()

    feats: dict = {
        # --- URL / host lexical (always available; the fallback for thin pages) ---
        "url_length": len(raw),
        "path_depth": len([p for p in path.split("/") if p]),
        "query_param_count": len([p for p in query.split("&") if p]),
        "url_digit_ratio": (sum(c.isdigit() for c in raw) / len(raw)) if raw else 0.0,
        "url_hyphen_count": raw.count("-"),
        "url_pct_encoded_count": len(re.findall(r"%[0-9a-fA-F]{2}", raw)),
        "host_entropy": round(_shannon(host), 4),
        "path_entropy": round(_shannon(path), 4),
        "subdomain_depth": max(0, len(host_labels) - 2),
        "is_ip_literal_host": 1 if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host or "") else 0,
        "is_cheap_tld": 1 if tld in _CHEAP_TLDS else 0,
        "kw_url_gambling": sum(tok in raw_l for tok in _GAMBLING_URL_TOKENS),
        # --- structure (filled below) ---
        "link_density": 0.0,
        "internal_link_ratio": 0.0,
        "paragraph_count": 0,
        "outbound_domain_diversity": 0,
        "link_count": 0,
        "dom_node_count": 0,
        "text_to_tag_ratio": 0.0,
        "max_dom_depth": 0,
        # --- tag histogram ---
        "tag_div": 0, "tag_iframe": 0, "tag_script": 0, "tag_form": 0,
        "tag_input": 0, "tag_a": 0, "tag_img": 0, "tag_canvas": 0, "tag_video": 0,
        # --- script composition ---
        "third_party_script_ratio": 0.0,
        "inline_script_ratio": 0.0,
        "script_host_entropy": 0.0,
        "popup_indicator_count": 0,
        # --- functional elements ---
        "has_url_like_input": False,
        "has_payment_field": False,
        "has_age_gate": bool(_AGE_GATE_RE.search(html or "")),
        "has_video_player": False,
        "has_dominant_canvas": False,
        "iframe_cross_origin_count": 0,
        "has_large_xorigin_iframe": False,
        # --- resource fingerprints ---
        "fp_gambling_affiliate_count": 0,
        "fp_crypto_widget_count": 0,
        "has_gambling_license_seal": bool(_LICENSE_SEAL_RE.search(html or "")),
        # --- text quality ---
        "body_char_count": 0,
        "glyph_cipher": False,
    }

    if not html:
        return feats

    try:
        soup = BeautifulSoup(html[:400000], "html.parser")
    except Exception:
        return feats

    feats["dom_node_count"] = len(soup.find_all(True))
    for name in ("div", "iframe", "script", "form", "input", "a", "img", "canvas", "video"):
        feats[f"tag_{name}"] = len(soup.find_all(name))
    lang = (soup.html.get("lang") if soup.html else "") or ""

    # --- everything that reads <script>/<iframe>/<img> MUST run before the strip below ---
    # scripts: 1st vs 3rd party, inline ratio, host entropy, popunder tells
    scripts = soup.find_all("script")
    with_src = [s for s in scripts if s.get("src")]
    host_counts: dict[str, int] = {}
    third_party = 0
    for s in with_src:
        m = re.match(r"(?:https?:)?//([^/]+)", s["src"].strip(), re.I)
        h = (m.group(1).lower() if m else "")
        if h:
            host_counts[h] = host_counts.get(h, 0) + 1
            if _registrable(h) and _registrable(h) != reg:
                third_party += 1
    for s in scripts:
        if not s.get("src") and _POPUNDER_RE.search(s.get_text() or ""):
            feats["popup_indicator_count"] += 1
    feats["third_party_script_ratio"] = round(third_party / len(with_src), 4) if with_src else 0.0
    feats["inline_script_ratio"] = round((len(scripts) - len(with_src)) / len(scripts), 4) if scripts else 0.0
    if host_counts:
        tot = sum(host_counts.values())
        feats["script_host_entropy"] = round(
            -sum((c / tot) * math.log2(c / tot) for c in host_counts.values()), 4
        )

    # iframes: cross-origin count + a "large" heuristic (width/height attrs, no DOM area)
    for f in soup.find_all("iframe"):
        src = (f.get("src") or "").strip()
        m = re.match(r"https?://([^/]+)", src, re.I)
        h = m.group(1).lower() if m else ""
        if h and _registrable(h) and _registrable(h) != reg:
            feats["iframe_cross_origin_count"] += 1
            if _looks_large(f):
                feats["has_large_xorigin_iframe"] = True

    feats["has_video_player"] = soup.find("video") is not None
    for c in soup.find_all("canvas"):
        if _looks_large(c):
            feats["has_dominant_canvas"] = True
            break

    # resource fingerprints over script + iframe + img src hosts
    src_blob = " ".join(
        (t.get("src") or t.get("data-src") or "") for t in soup.find_all(["script", "iframe", "img"])
    ).lower()
    feats["fp_gambling_affiliate_count"] = sum(m in src_blob for m in _FP_GAMBLING_AFFILIATE)
    feats["fp_crypto_widget_count"] = sum(m in src_blob for m in _FP_CRYPTO_WIDGET)

    # inputs: url-like box (rare on gambling, kept for parity) + payment fields
    for el in soup.find_all("input"):
        typ = (el.get("type") or "text").lower()
        idbag = " ".join(
            str(el.get(k) or "") for k in ("name", "id", "placeholder", "aria-label", "autocomplete")
        )
        if typ not in ("search", "hidden", "password", "checkbox", "radio", "submit"):
            if _URL_INPUT_RE.search(idbag) and not re.search(r"\bq\b|query|search|find", idbag, re.I):
                feats["has_url_like_input"] = True
        if _PAYMENT_FIELD_RE.search(idbag):
            feats["has_payment_field"] = True
    if not feats["has_payment_field"] and _PAYMENT_FIELD_RE.search(html[:200000]):
        feats["has_payment_field"] = True

    # --- now strip non-content tags and read the visible text ---
    for t in soup(["script", "style", "noscript", "template"]):
        t.extract()
    body_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    feats["body_char_count"] = len(body_text)
    body_chars = max(1, len(body_text))
    feats["text_to_tag_ratio"] = round(len(body_text) / max(1, feats["dom_node_count"]), 3)
    feats["glyph_cipher"] = detect_glyph_cipher(body_text, lang)

    # links: density + internal ratio + outbound diversity
    anchors = soup.find_all("a", href=True)
    feats["link_count"] = len(anchors)
    anchor_text_chars = 0
    internal = resolvable = 0
    ext_regs: set[str] = set()
    for a in anchors:
        anchor_text_chars += len(a.get_text(strip=True))
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        m = re.match(r"https?://([^/]+)", href, re.I)
        h = m.group(1).lower() if m else ""
        if not h and not href.startswith("//"):
            internal += 1
            resolvable += 1
            continue
        if not h and href.startswith("//"):
            h = href[2:].split("/")[0].lower()
        r = _registrable(h)
        if not r:
            continue
        resolvable += 1
        if r == reg:
            internal += 1
        else:
            ext_regs.add(r)
    feats["link_density"] = round(anchor_text_chars / body_chars, 4)
    feats["internal_link_ratio"] = round(internal / resolvable, 4) if resolvable else 0.0
    feats["outbound_domain_diversity"] = len(ext_regs)

    feats["paragraph_count"] = sum(
        1 for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40
    )
    feats["max_dom_depth"] = _max_depth(soup.body or soup)

    return feats


def _looks_large(tag) -> bool:
    """No DOM geometry from static HTML -- infer 'large' from width/height attrs or a
    100%/vh/vw style. Absent dimensions -> assume large (iframes/canvases with no
    explicit size usually fill their container)."""
    w = str(tag.get("width") or "")
    h = str(tag.get("height") or "")
    style = str(tag.get("style") or "").lower()
    if re.search(r"(width|height)\s*:\s*(100%|\d{3,}px|\d+v[wh])", style):
        return True
    def _big(v: str) -> bool:
        v = v.strip().lower()
        if v in ("", "100%") or v.endswith(("vw", "vh")):
            return v != ""
        m = re.match(r"(\d+)", v)
        return bool(m) and int(m.group(1)) >= 400
    if not w and not h:
        return True
    return _big(w) or _big(h)


def _max_depth(node, _d: int = 0, _budget: list | None = None) -> int:
    if _budget is None:
        _budget = [12000]
    if _budget[0] <= 0:
        return _d
    _budget[0] -= 1
    kids = getattr(node, "find_all", lambda *a, **k: [])(True, recursive=False)
    if not kids:
        return _d
    return max(_max_depth(k, _d + 1, _budget) for k in kids)


# --- glyph-cipher (font-substitution) detection ---------------------------
_LATIN_LANGS = {
    "en", "es", "fr", "de", "pt", "it", "nl", "sv", "da", "no", "nb", "nn", "fi",
    "is", "pl", "cs", "sk", "sl", "hr", "ro", "hu", "tr", "et", "lv", "lt", "ga",
    "cy", "ca", "gl", "eu", "af", "sw", "id", "ms", "tl", "vi", "lb", "mt",
}


def _classify_cp(cp: int):
    if 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A:
        return "ascii"
    if 0x30 <= cp <= 0x39:
        return "digit"
    if cp < 0x80:
        return None
    if (0x3400 <= cp <= 0x9FFF) or (0xF900 <= cp <= 0xFAFF) or (0x20000 <= cp <= 0x2FA1F):
        return "han"
    if 0xAC00 <= cp <= 0xD7A3:
        return "hangul"
    if (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0xFFFFD) or (0x100000 <= cp <= 0x10FFFD):
        return "pua"
    if 0x2000 <= cp <= 0x206F or 0x2190 <= cp <= 0x2BFF or 0x1F000 <= cp <= 0x1FFFF or 0xFE00 <= cp <= 0xFE0F:
        return None
    return "small"


def detect_glyph_cipher(text: str, lang: str = "") -> bool:
    """True if `text` looks like glyph-substitution-cipher obfuscation: a long body
    drawn from a tiny fixed alphabet, so distinct-codepoint count saturates while real
    prose keeps introducing new characters. Language-agnostic -- can't be evaded by
    spoofing the lang attribute. Mirrors Fenceline extension/lib/detect/glyph-cipher.js.
    """
    if not text:
        return False
    ascii_n = digit_n = han = hangul = small = pua = 0
    d_han: set[int] = set()
    d_hangul: set[int] = set()
    d_pua: set[int] = set()
    for ch in text:
        cp = ord(ch)
        k = _classify_cp(cp)
        if k == "ascii":
            ascii_n += 1
        elif k == "digit":
            digit_n += 1
        elif k == "han":
            han += 1; d_han.add(cp)
        elif k == "hangul":
            hangul += 1; d_hangul.add(cp)
        elif k == "pua":
            pua += 1; d_pua.add(cp)
        elif k == "small":
            small += 1
    total = ascii_n + han + hangul + small + pua
    if total < 80:
        return False
    if han >= 180 and len(d_han) <= 100 and len(d_han) * 2 < han and han >= 0.6 * total:
        return True
    if hangul >= 180 and len(d_hangul) <= 100 and len(d_hangul) * 2 < hangul and hangul >= 0.6 * total:
        return True
    if 150 <= pua and 15 <= len(d_pua) <= 100 and len(d_pua) * 2 < pua and pua >= 0.6 * total:
        return True
    p = str(lang or "").lower().split("-")[0]
    if p in _LATIN_LANGS and small >= 80 and small > 0.9 * total and ascii_n == 0 and digit_n == 0:
        return True
    return False


FEATURE_KEYS = tuple(structural_features("", "").keys())


def feature_vector(html: str, url: str = "") -> list[float]:
    """structural_features() as an ordered numeric list (bool -> 0/1), FEATURE_KEYS order."""
    f = structural_features(html, url)
    return [float(f[k]) if not isinstance(f[k], bool) else float(f[k]) for k in FEATURE_KEYS]


# --- self-check -------------------------------------------------------------
def _demo() -> int:
    operator = """<html lang="en"><head><title>LuckyStar Casino</title></head><body>
      <nav><a href="/sports">Sports</a><a href="/casino">Casino</a><a href="/slots">Slots</a>
      <a href="/promotions">Promotions</a><a href="/login">Login</a><a href="/register">Register</a></nav>
      <h1>Welcome bonus 300%</h1><p>Deposit now and claim your welcome bonus. Instant withdrawal.</p>
      <iframe src="https://games.pragmaticplay.net/loader" width="100%" height="800"></iframe>
      <script src="https://cdn.smartico.ai/w.js"></script>
      <input name="cardNumber" placeholder="Card number"><input name="cvv">
      <div>Licensed and regulated by the Curacao Gaming Authority. 18+ only. Are you 18?</div>
    </body></html>"""
    article = """<html lang="en"><head><title>How online casino bonuses work</title></head><body>
      <article><h1>How online casino wagering requirements work</h1>
      <p>Posted on March 3, 2026 by Priya Nair. 9 min read. Online casinos advertise a welcome
      bonus, but the wagering requirement is the catch. In this article we break down the maths
      behind a typical deposit bonus and show why the house edge still applies.</p>
      <p>Consider a 100% match up to a set amount. To withdraw the bonus a player must wager it
      a fixed multiple of times. We walk through a worked example step by step below.</p>
      <p>Related reading covers sports betting odds, poker variance, and responsible gambling
      tools. None of this is an endorsement to play.</p>
      <p>Leave a comment. Filed under: casino guides. About the author.</p></article>
    </body></html>"""

    fo = structural_features(operator, "https://luckystar-casino.xyz/")
    fa = structural_features(article, "https://gambling-explainer-blog.com/bonuses")

    assert fo["is_cheap_tld"] == 1 and fa["is_cheap_tld"] == 0
    assert fo["kw_url_gambling"] >= 1
    assert fo["has_payment_field"] and not fa["has_payment_field"]
    assert fo["has_gambling_license_seal"] and not fa["has_gambling_license_seal"]
    assert fo["has_age_gate"] and not fa["has_age_gate"]
    assert fo["iframe_cross_origin_count"] >= 1 and fo["has_large_xorigin_iframe"]
    assert fo["fp_gambling_affiliate_count"] >= 1
    # the article reads as prose: far lower link density, real paragraphs, no funnel
    assert fa["paragraph_count"] >= 3
    assert fa["link_density"] < fo["link_density"] or fo["link_density"] > 0.3
    assert len(feature_vector(operator, "https://x.casino/")) == len(FEATURE_KEYS)
    assert structural_features("", "") and structural_features("<x>", "") is not None  # no raise

    # glyph-cipher: long text from a tiny PUA alphabet -> flagged; real prose -> not
    cipher = "".join(chr(0xE000 + (i * 7 % 40)) for i in range(400))
    assert detect_glyph_cipher(cipher)
    assert not detect_glyph_cipher(article)
    assert not detect_glyph_cipher("short text")

    print(f"structural_features self-check: OK ({len(FEATURE_KEYS)} features, operator vs article separates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
