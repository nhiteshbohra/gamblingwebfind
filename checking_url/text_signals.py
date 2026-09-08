"""
checking_url/text_signals.py -- cheap regex/string signals over page + OCR text that
fire when the curated keyword list can't: an operator whose brand token is new,
obfuscated, or image-only still ships the same marketing copy ("min deposit 100,
instant withdrawal, bonus 100%, DANA/UPI, refer & earn, WhatsApp 62812...").

Four things, all pure functions of text:
  * merge_trailing_numbers  -- "garuda 123" -> "garuda123"  (betting-ID / brand+digits
                               shape: laser247, lotus365, cricbet99, 91club...)
  * contextual_score        -- monetary / urgency / payment-channel / refer-earn phrases
                               -> (score, reasons). Additive, capped -- meaningful but
                               never an instant lock on its own (like a WEAK signal).
  * extract_contacts        -- pull the actual WhatsApp / Telegram / phone VALUES
                               (needs a channel label next to the number, so a legit
                               site's office number isn't harvested). Signal + report intel.
  * text_density            -- non-space char count (Paper B: gambling pages ~2x denser).

(A fuzzy Levenshtein brand match was here and removed 2026-09-04 -- see the note
below; it needs a curated distinctive-brand list, not a keyword list.)

`text_signals()` runs all four for a fuse()/logging payload. Adapted from
GbDetector (github-projects-comparison-report.md, repo #4) + Paper B, widened for the
Indian/SEA market (rupees, UPI, Paytm/PhonePe/GPay, recharge, satta/matka copy).

Not wired into classifier.py yet -- standalone, feed it into the keyword score and
fusion.fuse() when you wire the fusion stage. See INTEGRATION.md.

    python -m checking_url.text_signals        # self-check
"""
from __future__ import annotations

import re

# --- 1. trailing-number merge -------------------------------------------
# Don't merge when the digits are an AMOUNT, not a brand-ID suffix: "bonus 100%",
# "deposit 100 rs", "win 5x", "50 free". Units are a closed set.
_TRAIL_UNIT = (
    r"(?!\s*(?:%|(?:rs|inr|rupees?|rupiah|idr|usdt?|usd|k|rb|ribu|juta|jt|lakh|cr|x|"
    r"times|guna|dollars?|taka|bdt|eur|gbp|per|off|free|percent|pts?|points?)\b))"
)
_MERGE_RE = re.compile(r"\b([a-z]{3,}[a-z0-9]*?)[\s._-]{1,3}(\d{2,6})\b" + _TRAIL_UNIT, re.I)
# Words that take a number as an AMOUNT/ARG, never as a brand suffix -- don't glue.
_NO_MERGE_LEAD = frozenset("""
deposit withdraw withdrawal bonus min max minimum maximum get win won pay add claim
spin only upto up over under above below rs inr usd first second third daily weekly
monthly free extra top total amount rate age year day days hour hours plus percent
""".split())


def merge_trailing_numbers(text: str) -> str:
    """Join a word to the digits right after it: 'garuda 123' -> 'garuda123',
    'laser 247' -> 'laser247'. Amount+unit ('bonus 100%', '50 rupees', 'win 5x') and
    verb+amount ('deposit 100') are left alone -- contextual_score handles those."""
    if not text:
        return ""
    return _MERGE_RE.sub(
        lambda m: (m.group(1) + m.group(2)) if m.group(1).lower() not in _NO_MERGE_LEAD
        else m.group(0),
        text,
    )


# --- 2. contextual indicators -----------------------------------------
_AMT = r"(?:\d[\d,]*\s*(?:k|rb|ribu|juta|jt|lakh|cr|rs|inr|₹|rupees?|rupiah|idr|usdt?|\$)?)"
_CTX = [
    # (weight, label, compiled regex)
    (0.15, "min_deposit", re.compile(
        r"\b(?:min(?:imum)?\.?\s*(?:dep(?:osit)?|deposito|recharge|top\s*up)|"
        r"dep(?:osit)?\s*min(?:imum)?|min\s*wd|wd\s*min)\b[^.\n]{0,20}" + _AMT, re.I)),
    (0.15, "bonus_pct", re.compile(
        r"\b(?:bonus|promo|cashback|rebate|extra|welcome\s*offer)\b[^.\n]{0,20}\d{2,3}\s*%", re.I)),
    (0.12, "win_multiplier", re.compile(
        r"\b(?:win|menang|jeeto|jito|untung|payout|profit)\b[^.\n]{0,15}\d+\s*(?:x|times|guna)\b", re.I)),
    (0.12, "instant_cashout", re.compile(
        r"\b(?:instant|fast|quick|24[x/ ]?7|same\s*day)\s*(?:withdrawal|payout|wd|cashout|keluar)\b", re.I)),
    (0.10, "wager_bonus_terms", re.compile(
        r"\b(?:wagering\s*requirement|turnover\s*requirement|rollover|no[- ]?deposit\s*bonus|free\s*bet)\b", re.I)),
    # urgency
    (0.08, "urgency", re.compile(
        r"\b(?:today|hari\s*ini|sekarang|abhi|aaj|right\s*now|limited\s*time|hurry|terbatas|jaldi)\b"
        r"[^.\n]{0,25}\b(?:bonus|promo|offer|free|claim)\b", re.I)),
    # payment channels (an operator lists many; a bank lists its own one)
    (0.10, "ewallet_list", re.compile(
        r"\b(?:dana|ovo|gopay|linkaja|shopeepay|pulsa)\b(?:[^.\n]{0,40}\b(?:dana|ovo|gopay|linkaja|shopeepay|pulsa)\b)", re.I)),
    (0.10, "upi_paytm", re.compile(
        r"\b(?:upi|paytm|phonepe|phone\s*pe|google\s*pay|gpay|bharatpe|imps|rtgs\s*deposit)\b"
        r"[^.\n]{0,25}\b(?:deposit|withdraw|recharge|add\s*(?:cash|money|fund))\b", re.I)),
    (0.08, "all_bank", re.compile(
        r"\b(?:all\s*bank|semua\s*bank|any\s*bank|bank\s*lokal|local\s*bank)\b[^.\n]{0,20}"
        r"\b(?:accept|support|deposit|available)\b", re.I)),
    # affiliate / growth loop
    (0.10, "refer_earn", re.compile(
        r"\b(?:refer(?:ral)?\s*(?:and|&|\s)\s*earn|refer\s*&\s*earn|referral\s*(?:bonus|code|link)|"
        r"invite\s*(?:friends?|and\s*earn))\b", re.I)),
    # betting-desk / id-issuing funnel language (very operator-specific)
    (0.15, "id_funnel", re.compile(
        r"\b(?:get\s*(?:your\s*)?(?:betting|cricket|demo|online)\s*id|create\s*(?:master|betting)\s*id|"
        r"whatsapp\s*(?:for|to\s*get)\s*id|book(?:ie)?\s*id|deposit\s*to\s*(?:get\s*)?id)\b", re.I)),
]
_CTX_CAP = 1.5  # meaningful, but never an instant lock alone


def contextual_score(text: str) -> tuple[float, list[str]]:
    """Additive score + matched labels. Capped at _CTX_CAP."""
    if not text:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    for w, label, rx in _CTX:
        if rx.search(text):
            score += w
            reasons.append(label)
    return round(min(score, _CTX_CAP), 3), reasons


# --- 3. contact extraction ------------------------------------------
_PHONE = r"(\+?\d[\d\s().-]{6,16}\d)"
_CONTACT_RES = {
    "whatsapp": (
        re.compile(r"\b(?:wa|w4|whats\s?app|whatsapp)\b[\s:.]*" + _PHONE, re.I),
        re.compile(r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)/?(\+?\d{8,15})", re.I),
    ),
    "telegram": (
        re.compile(r"\b(?:telegram|tele|tg)\b[\s:.]*(@[\w]{4,32}|\+?\d{8,15})", re.I),
        re.compile(r"(?:t\.me|telegram\.me)/(@?[\w]{4,32})", re.I),
    ),
    "phone": (
        re.compile(r"\b(?:call|contact|hubungi|hp|telp|tel|phone|kontak|मोबाइल|संपर्क)\b[\s:.]*" + _PHONE, re.I),
    ),
}


def _norm_num(v: str) -> str:
    v = v.strip()
    if v.startswith("@"):
        return v.lower()
    n = re.sub(r"[^\d+]", "", v)
    return n if len(re.sub(r"\D", "", n)) >= 8 else ""


def extract_contacts(text: str) -> dict[str, list[str]]:
    """{'whatsapp': [...], 'telegram': [...], 'phone': [...]} -- only values that sit
    next to a channel label, so a legit site's plain office number isn't harvested."""
    out: dict[str, list[str]] = {}
    if not text:
        return out
    for kind, rxs in _CONTACT_RES.items():
        vals: list[str] = []
        for rx in rxs:
            for m in rx.finditer(text):
                v = _norm_num(m.group(1))
                if v and v not in vals:
                    vals.append(v)
        if vals:
            out[kind] = vals[:10]
    return out


# --- 4. text density ------------------------------------------------
def text_density(text: str) -> int:
    """Non-space char count. Paper B: gambling pages ~2x denser than legit."""
    return len(re.sub(r"\s+", "", text or ""))


# NOTE: a fuzzy (Levenshtein) brand match lived here and was REMOVED 2026-09-04.
# Fuzzed against classifier.GAMBLING_DOMAIN_KEYWORDS it fired on every page containing
# the ordinary word "casino" ("casino"~"cazino" dist 1), "setting"~"betting",
# "casinos"~"casino", etc. -- it put iGaming trade-press, the Ontario regulator, casino
# resorts and a surf blog into the gambling/ folder in a real visual_check run. It only
# ever made sense against a curated DISTINCTIVE-brand list (parimatch/dafabet/1xbet...),
# not a generic keyword list, and even then needs an eval. Re-add with that, not this.


# --- combined ------------------------------------------------------
def text_signals(text: str, ocr_text: str = "") -> dict:
    """One call -> every text signal, for a fuse() input / audit log."""
    raw = f"{text or ''} {ocr_text or ''}".strip()
    blob = merge_trailing_numbers(raw)
    # contextual + contacts read the ORIGINAL phrasing ("min deposit 100"); merged form
    # ("garuda123") is only for a downstream keyword scan via `merged_text`.
    score, reasons = contextual_score(raw)
    contacts = extract_contacts(raw)
    return {
        "contextual_score": score,
        "contextual_reasons": reasons,
        "contacts": contacts,
        "contact_channels": sorted(contacts.keys()),
        "text_density": text_density(text or ""),
        "ocr_text_density": text_density(ocr_text or ""),
        "merged_text": blob,
    }


# --- self-check ----------------------------------------------------
def _demo() -> int:
    spam = (
        "Welcome to garuda 123 the most trusted site. Min deposit Rs 100, bonus 100% "
        "on first deposit. Instant withdrawal 24x7. DANA OVO GoPay Paytm UPI accepted. "
        "Refer and earn 5% for life. WhatsApp: +62 812-3456-7890 or telegram @garudaadmin. "
        "Get your cricket ID now. laser 247 aviator teen patti."
    )
    clean = (
        "Springfield Community Bank -- personal banking, savings account, home loan. "
        "Our branch is open Mon-Fri. For account help call 1-800-555-0100 during business hours. "
        "Interest rates effective April 2026. Member FDIC."
    )

    s = text_signals(spam)
    assert s["contextual_score"] >= 0.6, s
    assert {"min_deposit", "bonus_pct", "refer_earn"} <= set(s["contextual_reasons"]), s["contextual_reasons"]
    assert "62812345678" in "".join(s["contacts"].get("whatsapp", [])), s["contacts"]
    assert "@garudaadmin" in s["contacts"].get("telegram", []), s["contacts"]
    assert "garuda123" in s["merged_text"] and "laser247" in s["merged_text"], s["merged_text"]

    c = text_signals(clean)
    assert c["contextual_score"] == 0.0, c
    # a labelled phone can still be picked up -- that's fine, contacts alone aren't a verdict
    assert c["contextual_reasons"] == []

    # merge leaves units/percent alone
    assert merge_trailing_numbers("bonus 100 % cashback 50 rupees") == "bonus 100 % cashback 50 rupees"
    assert merge_trailing_numbers("win99 club") == "win99 club"
    assert text_density("a b c") == 3
    assert "fuzzy_brand_score" not in s, "fuzzy brand match was removed (FP-prone) -- do not re-add against a keyword list"

    print(f"text_signals self-check: OK (spam score={s['contextual_score']} reasons={s['contextual_reasons']}; "
          f"clean score={c['contextual_score']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
