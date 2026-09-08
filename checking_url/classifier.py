"""
checking_url/classifier.py — High-Accuracy Heuristic Pre-Classifier.

Rules (Updated Architecture — Triple-Lock):
- Dead / Blocked -> handle upstream
- Parked / For-Sale landers -> instant "regular" (reachable, not gambling, not dead)
- Negative Archetypes (Educational, E-Commerce, News/Wiki) -> auto-regular ONLY if >= 4 signals AND 0 keywords
- Score (STRONG keywords weighted 1.0, WEAK 0.5) >= 4.0 AND a strong/actionable signal is
  present -> "gambling" (confirmed immediately, no AI needed)
- Any strong signal present at all (regardless of total score) -> "needs_ai" (escalated to
  Ollama AI Challenge Round)
- Hospitality/negative-archetype pages with score < 1.5 and no hard actionable signal ->
  "regular" (no AI needed)
- Everything else -> "regular" (no AI needed)
"""
import json
import warnings
from pathlib import Path
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TRUSTED_DOMAINS_CACHE: set | None = None


def load_trusted_domains() -> set:
    """Load the institutional allowlist (trusted_domains.json) — domains that skip
    classification entirely and are always 'regular', no matter what the heuristics or AI
    would otherwise say. Reserved for domains you are certain about (major banks,
    regulators, government bodies) since a match here bypasses every other safety check."""
    global _TRUSTED_DOMAINS_CACHE
    if _TRUSTED_DOMAINS_CACHE is not None:
        return _TRUSTED_DOMAINS_CACHE
    path = PROJECT_ROOT / "trusted_domains.json"
    domains = set()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            domains = set(str(d).strip().lower().removeprefix("www.") for d in data.get("domains", []) if d)
        except Exception as e:
            print(f"[classifier WARNING] Failed to load trusted_domains.json: {e}")
    _TRUSTED_DOMAINS_CACHE = domains
    return domains


def is_trusted_domain(url_or_domain: str) -> bool:
    """Check the registrable domain (and its parent, for subdomains) against the allowlist."""
    if not url_or_domain:
        return False
    trusted = load_trusted_domains()
    if not trusted:
        return False
    clean = url_or_domain.lower()
    clean = clean.split("://")[-1].split("/")[0].split(":")[0].removeprefix("www.")
    if clean in trusted:
        return True
    # Allow a matched parent domain to cover subdomains (e.g. netbanking.hdfcbank.com)
    parts = clean.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in trusted:
            return True
    return False


def _get_keywords_file() -> Path:
    candidates = list(PROJECT_ROOT.glob("gambling_top_*_keywords.json")) + list(PROJECT_ROOT.glob("*keyword*.json"))
    return candidates[0] if candidates else (PROJECT_ROOT / "gambling_top_944_keywords.json")

# Fast-path hardcoded gambling signals (critical terms that should never be missed)
# These are checked BEFORE loading the full JSON for speed on high-confidence terms
FAST_PATH_GAMBLING_SIGNALS = {
    # Lottery
    "lottery", "lotto", "bhagyashree", "bhagyalaxmi", "bhagya lottery", "lottery result",
    "lottery prediction", "online lottery", "lucky draw", "prize draw", "scratch card",
    "instant lottery", "nagaland lottery", "sikkim lottery", "kerala lottery",
    # Blackjack / Card games
    "blackjack", "21count", "card counting", "pontoon", "hi-lo",
    # Deposit / Bonus triggers
    "no-deposit", "no deposit bonus",
    "free bonus", "cashback on losses", "prize pool", "rebate",
    # Indian Satta extensions
    "fix satta", "fix matka", "jodi chart", "panel chart", "half sangam",
    "full sangam", "open close", "matka result", "disawar result",
    # Specific site brands
    "21 blackjack", "video poker", "poker trainer", "real money app",
    "play for real money", "win real money", "real cash games",
}

# Bare category words -- "casino"/"slot"/"poker"/etc with no qualifying phrase around them.
# Real enough to justify an AI look (has_strong_signals) but too low-specificity to ever
# count toward the score>=4.0 instant-lock on their own -- see the score computation and
# the STRONG_GAMBLING_SIGNALS comment below for why.
BARE_CATEGORY_SIGNALS = {"casino", "slot", "slots", "poker", "bingo", "roulette", "baccarat"}

# STRONG gambling signals — unambiguous, context-independent gambling terms
# If matched, AI timeout defaults to GAMBLING
STRONG_GAMBLING_SIGNALS = {
    "online casino", "online casinos", "online gambling", "casino games", "live casino",
    "sports betting", "sports bets", "betting exchange", "sportsbook", "sportsbooks",
    "bet now", "betting odds", "football betting", "cricket betting", "tennis betting",
    "upi deposit", "upi casino", "paytm casino", "phonepay casino",
    "satta matka", "satta king", "kalyan matka", "disawar result", "fix satta", "fix matka",
    "online poker", "poker tournament real money", "poker cash game",
    "online blackjack", "play blackjack", "blackjack table", "card counting",
    "slot machines", "slot games", "video slots", "claim free spins",
    "online lottery", "lottery result", "lottery prediction", "lucky draw winner",
    "betting id", "demo id", "whatsapp betting", "telegram betting",
    "wagering requirement", "wagering requirements", "bonus wagering",
    "cashback on losses", "no deposit bonus", "no-deposit bonus",
    "casino vip", "vip casino", "live baccarat", "live roulette", "live dealer",
    "crash game", "aviator game", "aviator betting", "aviator crash", "aviator predictor",
    "teen patti", "teenpatti", "teen patti master", "teen patti gold", "teen patti real cash",
    "andar bahar", "jhandi munda", "dragon tiger", "7 up down", "car roulette",
    "mines game", "plinko", "limbo", "responsible gambling",
    "wingogame", "wingo", "color prediction", "colour prediction", "colour trading", "color trading",
    "daman game", "daman games", "tiranga game", "tc lottery", "bdg game", "91 club", "91club", "goa games", "goagames", "big daddy game",
    "rummy", "rummy cash", "play rummy", "indian rummy", "rummy gold", "rummy modern",
    "casino app", "betting app",
    "crypto casino", "bitcoin casino", "usdt betting",
    "prop bets", "parlay bet", "accumulator bet", "money line",
    "jodi chart", "panel chart", "half sangam", "full sangam",
    "win real money", "real cash games", "play for real money", "real money app", "win real cash", "real cash app", "paisa jeeto",
    "download apk", "download android app", "instant upi withdrawal", "upi withdrawal", "paytm withdrawal", "min withdrawal", "min recharge",
    "ludo cash", "ludo real money",
    # Named gambling regulators / self-exclusion schemes -- added 2026-08-24, borrowed
    # from a browser-extension gambling classifier (Fenceline) that uses a similar
    # regulatory-seal regex as a structural feature. Placed in STRONG (not the
    # ACTIONABLE_WAGERING_SIGNALS_* sets above) deliberately: STRONG signals feed
    # has_strong_signals, which -- unlike has_hard_online_signals -- never bypasses
    # the negative-archetype/hospitality safety gate on its own (see the is_neg/
    # is_hosp branches below), so a review/affiliate site that merely *mentions* a
    # named regulator still correctly falls through to needs_ai instead of an
    # instant lock. This targets a real, already-documented false negative: a live
    # UK Gambling Commission-licensed operator that scored only 1.5 and was missed
    # by the old score>=2.0 co-requirement (see the fix noted further down in this
    # file) -- a licensed operator naming its own regulator is exactly the kind of
    # page the vernacular keyword list (satta/matka/teen patti/etc.) can miss.
    "uk gambling commission", "malta gaming authority", "curacao gaming license",
    "curacao egaming", "kahnawake gaming commission", "gamstop self-exclusion",
    "begambleaware.org", "gamcare.org.uk", "isle of man gambling supervision",
    # Bare category words -- added 2026-08-28. The ~944-term list above is almost entirely
    # multi-word phrases ("casino app", "casino bonus code"...), so a real operator whose
    # nav bar just says "Casino" / "Slot" / "Poker" (an extremely common terse-nav-tab UI
    # pattern on actual betting sites) scored ZERO keywords and never even reached AI
    # review -- confirmed live on 1xball.co, whose real page (Sports/Casino/Slot/Table tabs,
    # Register/Login, 18+ and GamCare badges) is an unambiguous sportsbook+casino operator
    # that locked to "regular" on "0 keywords matched". Safe to add as STRONG rather than a
    # new gate: per the has_strong_signals logic below, a lone hit here only ever routes to
    # needs_ai (AI review), same as the existing "responsible gambling" precedent -- it can
    # never instant-lock "gambling" by itself, and the hospitality/negative-archetype gate
    # above (score < 1.5) still catches a single incidental mention on an unrelated site.
    #
    # BUT: these bare words alone must never be allowed to accumulate toward the score>=4.0
    # instant-lock either (see BARE_CATEGORY_SIGNALS / score computation below) -- found live
    # on ibinfra.in, a construction firm with injected spam blog-post titles ("vulkanvegas
    # casino", "roulette bets australia", "cash billionaire slots", "100 free spins bonus")
    # from what looks like a site compromise. 6 bare/weak matches summed past 4.0 and
    # instant-locked to "gambling" with ZERO AI review -- the exact bypass the score>=4.0
    # fast-path is supposed to reserve for genuinely unambiguous multi-signal evidence.
} | BARE_CATEGORY_SIGNALS

# Actionable wagering signals — required to override confirmed news/editorial/regulatory sites.
# Split into unambiguous phrases (never appear on a legit bank/e-commerce/hospitality site) vs
# generic finance/retail vocabulary that ALSO needs a co-occurring gambling-context word before
# it counts — "cashier"/"instant withdrawal"/"deposit now" are completely normal on a bank's
# net-banking or ATM page and were previously enough, on their own, to null out a verified
# commercial-banking archetype match and auto-lock a false "gambling" verdict.
ACTIONABLE_WAGERING_SIGNALS_UNAMBIGUOUS = {
    "play for real money", "win real money", "real cash games", "real money app",
    "claim bonus now", "claim welcome bonus",
    "betting id", "demo id", "whatsapp betting", "telegram betting", "place bet now",
    "aviator crash", "dragon tiger live", "teen patti live", "jodi chart", "panel chart",
    # "wagering requirement"/"bonus wagering" removed 2026-08-30 -- unlike the phrases
    # above, these are T&C-disclosure language a gambling REVIEW/affiliate site legitimately
    # quotes about OTHER operators' bonuses (that's the site's entire subject matter), not
    # proof this site itself has a wagering mechanism. Confirmed live on
    # casinobonuschecker.com (a review site with no login/register of its own, "Visit X
    # Casino" outbound buttons) -- these two phrases were overriding the negative-archetype
    # exemption and instant-locking it to "gambling" with zero AI review. Both terms are
    # still in STRONG_GAMBLING_SIGNALS, so they still count toward matching/scoring --
    # removing them here only stops them from forcing an instant lock past that exemption.
}
ACTIONABLE_WAGERING_SIGNALS_GENERIC = {
    "deposit money", "deposit now", "instant deposit", "deposit funds",
    "withdraw money", "instant withdrawal", "withdrawal request", "cashier",
}
ACTIONABLE_WAGERING_SIGNALS = ACTIONABLE_WAGERING_SIGNALS_UNAMBIGUOUS | ACTIONABLE_WAGERING_SIGNALS_GENERIC

# Game / chart NAME phrases inside the UNAMBIGUOUS set that a blog / news story / wiki
# ABOUT gambling quotes descriptively rather than as a working CTA ("how the Aviator crash
# game works", "reading a satta panel chart"). Excluded when deciding whether an EDITORIAL
# page has a wagering mechanism of its OWN -- they still count everywhere else.
_ACTIONABLE_GAME_NAME_PHRASES = {
    "aviator crash", "dragon tiger live", "teen patti live", "jodi chart", "panel chart",
}

# Gambling-context words that must co-occur with a GENERIC actionable phrase for it to count
_ACTIONABLE_SIGNAL_CONTEXT = {
    "bet", "betting", "casino", "wager", "wagering", "sportsbook", "bookmaker",
    "satta", "matka", "poker", "rummy", "aviator", "odds", "gambling",
}

# WEAK / AMBIGUOUS signals — can appear on non-gambling sites too
# Weighted as 0.5 points toward the keyword threshold
WEAK_GAMBLING_SIGNALS = {
    "rebate",
    "sic bo",
    "free spins",
    "free bonus",
    "bonus",
    "deposit bonus",   # heavy on Forex broker signup offers — not unambiguous gambling
    "lottery",
    "jackpot",
    "odds",
    "prize",
    "win",
    "stake",
    "bet",
    "daily jackpot",
}


def load_keywords() -> set:
    """Load keywords from gambling_top_*_keywords.json into a set."""
    kw_file = _get_keywords_file()
    if kw_file.exists():
        with open(kw_file, "r", encoding="utf-8") as f:
            terms = json.load(f)
            kw_set = set(str(kw).strip().lower() for kw in terms if kw)
            kw_set.update(FAST_PATH_GAMBLING_SIGNALS)
            kw_set.update(STRONG_GAMBLING_SIGNALS)
            kw_set.update(WEAK_GAMBLING_SIGNALS)
            print(f"[classifier] Loaded {len(kw_set)} keywords from {kw_file.name}")
            return kw_set
    print(f"[classifier WARNING] {kw_file.name} not found — using fast-path signals only!")
    return set(FAST_PATH_GAMBLING_SIGNALS) | STRONG_GAMBLING_SIGNALS | WEAK_GAMBLING_SIGNALS


import re

def _extract_text(html: str) -> str:
    """Return visible text + title + meta description, lowercased with memory safety."""
    if not html:
        return ""
    
    # Cap HTML size at 350KB to eliminate MemoryError on binary/bloated payloads
    truncated_html = html[:350000]
    
    try:
        # Fast regex pre-strip heavy tags before passing to BeautifulSoup to avoid event-loop blocking
        cleaned_raw = re.sub(r'<(script|style|svg|noscript|iframe|path)[^>]*>[\s\S]*?</\1>', ' ', truncated_html, flags=re.IGNORECASE)
        if "<" not in cleaned_raw:
            return cleaned_raw.lower().strip()
        soup = BeautifulSoup(cleaned_raw, 'html.parser')
        parts = [soup.get_text(separator=' ')]
        title = soup.find('title')
        if title and title.string:
            parts.append(title.string)
        for meta in soup.find_all('meta', attrs={'name': True, 'content': True}):
            if meta['name'].lower() in ('description', 'keywords', 'og:description', 'og:title'):
                parts.append(meta['content'])
        # img alt text is invisible to get_text() (it only returns text NODES, never attribute
        # values) but carries real content on image-heavy pages — banner carousels, promo
        # graphics, and jackpot/game artwork routinely carry their marketing copy ONLY as alt
        # text for SEO/accessibility, with zero corresponding visible text node. A gambling
        # site whose promotional copy lives entirely in banner alt text was previously
        # scoring as if that copy didn't exist at all.
        for img in soup.find_all('img', attrs={'alt': True}):
            alt_text = img['alt'].strip()
            if alt_text:
                parts.append(alt_text)
        return ' '.join(parts).lower()
    except Exception:
        # Fallback fast regex stripper if BeautifulSoup fails
        clean = re.sub(r'<[^>]+>', ' ', truncated_html)
        return re.sub(r'\s+', ' ', clean).lower()


# ── Obfuscation-aware keyword matching ──────────────────────────────────────
# Gambling sites that want to dodge exactly this kind of keyword scanner
# routinely obfuscate the gambling terms themselves (leetspeak digit/symbol
# substitution, letter-spacing) while leaving everything else readable -- a
# well-documented real-world evasion tactic (see e.g. the paper this project
# was benchmarked against, which discusses the same attack against
# keyword/blacklist-based detectors). Straight lowercase + word-boundary
# regex matching against `text` has zero defense against it. Added
# 2026-08-24, borrowed from a comment-spam gambling detector (GbDetector)
# that already handles this for a different input (comment text, not page
# text) via the same substitution-map approach.
# "1" is ambiguous (stands in for both "i" and "l" depending on the word --
# "s1ot"->slot needs 1->l, but e.g. "b1tcoin" style terms need 1->i), so we
# generate both variants and check keywords against each rather than picking
# one substitution and silently missing the other half of real-world usage.
_LEETSPEAK_MAP_I = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b", "@": "a", "$": "s", "!": "i",
})
_LEETSPEAK_MAP_L = str.maketrans({
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b", "@": "a", "$": "s", "!": "i",
})
_LETTER_SPACING_RE = re.compile(r"\b(?:[a-z][\s\-_.]){2,}[a-z]\b")


def _deobfuscate_text(text: str) -> list[str]:
    """Return de-obfuscated pass(es) over `text` for keyword re-matching only.
    Never replaces the original `text` used for archetype/hospitality gates or
    OCR-original scoring -- this is purely an additional signal source, so a
    keyword that only appears once de-obfuscation is applied still has to pass
    the exact same word-boundary keyword match as normal text."""
    if not text:
        return []
    # Collapse intentionally letter-spaced runs (e.g. "s a t t a" or "c-a-s-i-n-o")
    # back into contiguous words before leetspeak substitution, then normalize
    # whitespace so a multi-word keyword phrase (e.g. "satta matka") still matches
    # with a single space between the two now-collapsed words.
    collapsed = _LETTER_SPACING_RE.sub(lambda m: re.sub(r"[\s\-_.]", "", m.group(0)), text)
    collapsed = re.sub(r"\s+", " ", collapsed)
    return [collapsed.translate(_LEETSPEAK_MAP_I), collapsed.translate(_LEETSPEAK_MAP_L)]


# Common markers for parked domains, registrars, and for-sale landers
PARKED_AND_FOR_SALE_MARKERS = [
    "is for sale", "domain for sale", "this domain is for sale",
    "this domain is available for sale", "the domain name is available for sale",
    "domain is available for purchase", "buy this domain", "purchase this domain",
    "purchase domain", "inquire about this domain", "make an offer on this domain",
    "make an offer", "domain portfolio for sale", "this domain may be for sale",
    "premium domain for sale", "domain name marketplace", "domain broker",
    "fast domain transfers", "domain name has been registered",
    "domain is registered at dynadot", "this domain is registered at dynadot",
    "get this domain", "bid on this domain",
    "domain parking", "domain is parked", "parked domain", "parked by",
    "parked free, courtesy of", "this webpage was generated by the domain owner using sedo",
    "this page is parked free", "this domain is registered and parked",
    "welcome to the future home of", "website coming soon", "under construction",
    "website is ready. the content is to be added",
    "related searches:", "related searches listing",
    "domain expired", "domain renewal", "default web site page",
    "domains.atom.com", "atom.com/domain", "dan.com/domain", "sedo.com", "sedoparking.com",
    "afternic.com/domain", "hugedomains.com/domain", "squadhelp.com/domain", "undeveloped.com",
    "domainmarket.com", "bodis.com", "parkingcrew.net", "parkingcrew.com",
    "above.com/parking", "buydomains.com", "domainagent.com", "godaddy.com/domains",
    "namecheap.com/domains", "uniregistry.com/domain", "voodoo.com/parking",
    # ParkLogic parking template -- bare topical link list ("Style & Fashion",
    # "Education", "Movies"...) with a "© <year> ParkLogic.com. All rights reserved."
    # footer and nothing else. No "for sale" wording, so it dodged every marker above
    # and a casino/bet-named domain then got AI-confirmed on the domain token alone
    # (freshbetcasino.org incident, 2026-09-03).
    "parklogic.com", "parklogic",
]

# Negative archetype indicators — only trigger if >= 4 signals AND 0 gambling keywords
NEGATIVE_ARCHETYPES = {
    "educational": [
        "admission", "facult", "academic", "syllabus", "curriculum",
        "degree college", "undergraduate", "postgraduate", "ph.d",
        "examination result", "chancellor", "principal", "alumni", "merit list"
    ],
    "ecommerce": [
        "add to cart", "shopping cart", "in stock", "out of stock",
        "free shipping", "return policy", "customer reviews", "product description",
        "delivery charges", "order summary", "secure checkout", "payment options"
    ],
    "news_media_journalism": [
        "breaking news", "editorial team", "journalism", "press release",
        "published by", "published on", "written by", "author:", "editor:",
        "news agency", "daily news", "news desk", "read full article", "read full story",
        "latest updates", "newsroom", "opinion column", "investigative report",
        "times of", "daily post", "tribune", "gazette", "herald", "chronicle",
        "guardian", "independent news", "media network", "syndicated news",
        "news feed", "newsletter subscription", "reuters", "associated press",
        "bbc news", "cnn", "ndtv", "hindustan times", "indian express",
        "fandom wiki", "community wiki", "table of contents", "wiki"
    ],
    "manufacturing_automotive": [
        "engine", "horsepower", "transmission", "torque", "cylinder",
        "outboard", "inboard", "marine engine", "boat engine", "dealership",
        "warranty", "spare parts", "service center", "authorized dealer",
        "car model", "two wheeler", "four wheeler", "showroom",
        "industrial robots", "motion control", "servo drives", "inverter drives",
        "automation systems", "machine controllers", "factory automation", "robotics"
    ],
    "ngo_charity_foundation": [
        "ngo", "non-profit", "nonprofit", "charity", "donate", "donation",
        "philanthropy", "volunteer", "rescue stories", "impact report",
        "empowerment", "humanitarian", "fundraising", "community outreach",
        "child development", "social welfare", "csr initiative", "foundation"
    ],
    "food_travel_lifestyle": [
        "restaurant", "cuisine", "recipe", "dish", "menu", "chef",
        "travel guide", "destination", "hotel review", "food blog",
        "best restaurants", "local food", "street food", "food atlas",
        "trip advisor", "travel tips", "itinerary"
    ],
    "tech_blog_crypto_news": [
        "app review", "tech news", "gadget review", "software review",
        "blockchain news", "crypto news", "defi news", "nft news",
        "market cap", "trading volume", "coin price", "medium.com", "substack"
    ],
    "business_org_chamber": [
        "chamber of commerce", "business association", "member directory",
        "annual report", "board of directors", "non profit", "nonprofit",
        "community organization", "workforce development", "job placement",
        "small business", "business directory", "civic organization"
    ],
    "calculator_utility": [
        "calculator", "calculate", "gst calculator", "tax calculator", "emi calculator",
        "loan calculator", "mortgage calculator", "percentage calculator", "age calculator",
        "bmi calculator", "calorie calculator", "currency converter", "unit converter",
        "scientific calculator", "financial calculator", "salary calculator", "sip calculator",
        "interest calculator", "fd calculator", "rd calculator", "retirement calculator",
        "calculate online", "math calculator", "conversion tool"
    ],
    "trading_fintech": [
        "forex", "foreign exchange", "cfds", "cfd trading", "spread betting",
        "pip", "lot size", "leverage", "margin call", "stop loss", "take profit",
        "mt4", "mt5", "metatrader", "trading platform", "trading account",
        "stock market", "equity trading", "share market", "nifty", "sensex",
        "mutual fund", "sip", "demat account", "brokerage", "sebi registered",
        "sec regulated", "fca regulated", "asic regulated", "cysec", "regulated broker",
        "technical analysis", "fundamental analysis", "candlestick", "chart pattern",
        "risk management", "portfolio", "asset management", "wealth management",
        "trade now", "open an account", "demo account", "live account",
    ],
    "regulatory_audit_testing": [
        "accredited testing", "testing laboratory", "testing agency", "certification body",
        "regulatory compliance", "approved test house", "independent testing",
        "regulatory authority", "gaming authority", "gambling commission",
        "player protection", "standards and compliance", "compliance audit",
        "ecogra", "gaming laboratories international", "bmm testlabs", "iteclabs",
        "dispute resolution", "alternative dispute resolution", "adr entity", "certification"
    ],
    "insurance_corporate": [
        "insurance", "policyholder", "underwriting", "claims", "coverage",
        "life insurance", "health insurance", "property insurance", "casualty",
        "reinsurance", "premium payments", "chubb", "file a claim", "quote"
    ],
    # A site that REVIEWS, RANKS, or COMPARES gambling operators (affiliate/SEO content
    # sites) is, by design, saturated with the exact same STRONG_GAMBLING_SIGNALS phrases
    # ("online casino", "sports betting", "deposit bonus"...) as a real operator, so it was
    # previously indistinguishable from one and got auto-locked to "gambling" with zero AI
    # review via the score>=4.0 + has_strong_signals path. This archetype exists specifically
    # to route that case to needs_ai instead of an instant unreviewed lock — it does NOT
    # force "regular" (a review site with a real deposit/betting CTA of its own still needs
    # scrutiny), it only removes the false certainty of an instant lock.
    "gambling_review_affiliate": [
        "editor rating", "editor's pick", "expert review", "in-depth review",
        "read our review", "read full review", "affiliate disclosure",
        "we may earn commission", "we may earn a commission", "compare casinos",
        "casino comparison", "top rated casinos", "casino ranking", "our ranking",
        "betting site reviews", "sportsbook reviews", "how we rate", "review methodology",
        "our top picks", "compare bonuses", "bonus comparison", "trusted casino reviews",
        "independent reviews", "best online casinos", "best betting sites",
        # Added 2026-08-30 (casinobonuschecker.com incident) -- boilerplate wording common
        # across the bonus-comparison-site industry generally, not overfit to one domain.
        "the best casino bonuses", "compare the best casino", "check and compare",
        "personally play and review", "playing and reviewing", "we personally test",
    ],
    "commercial_banking": [
        "personal banking", "agri banking", "nri banking", "business banking",
        "savings account", "current account", "fixed deposit", "recurring deposit",
        "net banking", "netbanking", "mobile banking", "debit card", "credit card",
        "branch locator", "atm locator", "ifsc", "ifsc code", "rtgs", "neft", "imps",
        "rbi regulated", "interest rates", "loan against", "home loan", "car loan",
        "personal loan", "corporate banking", "wholesale banking", "rural banking",
        "deposit interest", "fixed deposits", "bank branch", "internet banking",
    ],
    "video_gaming_entertainment": [
        "playstation", "xbox", "nintendo switch", "nintendo", "steam", "epic games",
        "gameplay walkthrough", "patch notes", "system requirements", "game developer",
        "game studio", "indie game", "board game rental", "board games", "tabletop games",
        "carnival board games", "game engine", "mod download", "pc game", "console game",
        "video game review", "esports tournament", "game walkthrough", "speedrun",
    ],
}


def is_parked_or_for_sale(text: str, html: str = "", url: str = "") -> tuple[bool, list[str]]:
    """Check if the page is a parked domain, domain-for-sale lander, or marketplace."""
    text_lower = text.lower() if text else ""
    html_lower = html.lower() if html else ""
    url_lower = url.lower() if url else ""

    matched_markers = []
    for marker in PARKED_AND_FOR_SALE_MARKERS:
        if marker in text_lower or marker in html_lower or marker in url_lower:
            matched_markers.append(marker)

    if matched_markers:
        return True, matched_markers
    return False, []


DEAD_AND_ERROR_PAGE_MARKERS = [
    "ssl handshake failed", "error code 525", "error code 520", "error code 521",
    "error code 522", "error code 523", "error code 524", "web server is down",
    "host error", "origin unreachable", "verify you are human", "checking your browser",
    "welcome to nginx", "nginx web server is successfully installed",
    "apache2 ubuntu default page", "it works!", "iis windows server",
    "this domain has expired", "domain has expired", "dynadot auctions",
    "domain is expired", "account suspended", "website under maintenance",
    "server error 500", "502 bad gateway", "503 service temporarily unavailable",
    "504 gateway timeout", "page not found 404", "this site can't be reached",
    # Regulatory / geo-block interstitials -- a full-page notice served instead of the
    # site because gambling is unlicensed in the visitor's jurisdiction. No functional
    # gambling interface is present, so these are "blocked", not "gambling" -- but a
    # casino/bet-named domain would otherwise be AI-confirmed on the domain token plus
    # the word "gambling" in the blocking notice itself (casinacho-casino.org /
    # casinokingdoms.net / coolzinocasino.org incident, 2026-09-03).
    "access to this website is restricted", "access to this website from",
    "access from canada is blocked", "is blocked to remain compliant",
    "strona jest niedostępna", "strona jest niedostepna",
    "dostęp do tej strony został ograniczony", "dostep do tej strony zostal ograniczony",
]


def is_dead_or_error_page(text: str) -> tuple[bool, str]:
    """Check if the text indicates a dead site, captive portal, or server error page."""
    text_lower = text.lower() if text else ""
    # Strip non-alphanumeric for whitespace-resilient matching (e.g. 'welcometonginx')
    text_compact = re.sub(r"[^a-z0-9]", "", text_lower)

    for marker in DEAD_AND_ERROR_PAGE_MARKERS:
        marker_compact = re.sub(r"[^a-z0-9]", "", marker)
        if marker in text_lower or (len(marker_compact) >= 8 and marker_compact in text_compact):
            return True, marker

    # Check common server default and error patterns regardless of OCR spacing
    if "nginx" in text_compact and any(w in text_compact for w in ("welcome", "webserver", "successfullyinstalled")):
        return True, "nginx default page"
    if "apache" in text_compact and any(w in text_compact for w in ("itworks", "ubuntudefault", "defaultpage")):
        return True, "apache default page"
    if "cloudflare" in text_compact and any(w in text_compact for w in ("sslhandshake", "errorcode5", "verifyyouarehuman")):
        return True, "cloudflare error/turnstile"

    return False, ""



def detect_negative_archetype(text: str) -> tuple[bool, str]:
    """Check if the text belongs to a non-gambling archetype. Returns (is_negative, reason)."""
    text = text.lower() if text else ""
    
    for archetype, signals in NEGATIVE_ARCHETYPES.items():
        hits = [kw for kw in signals if re.search(rf"\b{re.escape(kw)}\b", text)]
        
        # Trading/Fintech & Commercial Banking need 3+ domain-specific signals to override (prevents deposit option lists from triggering false banking archetype)
        if archetype in ("trading_fintech", "commercial_banking"):
            if len(hits) >= 3:
                return True, f"archetype_{archetype} ({', '.join(hits[:3])})"
            continue
            
        if len(hits) >= 2:
            return True, f"archetype_{archetype} ({', '.join(hits[:2])})"
            
    return False, ""


# Hospitality / Food & Dining override signals
HOSPITALITY_OVERRIDE_SIGNALS = {
    "hotel", "resort", "motel", "inn", "lodge", "hostel", "bed and breakfast", "b&b",
    "book a room", "room booking", "check-in", "check-out", "check in", "check out",
    "room rates", "room rate", "per night", "nightly rate", "hotel room", "guest room",
    "suite", "deluxe room", "standard room", "double room", "single room", "twin room",
    "amenities", "concierge", "room service", "valet parking", "free parking",
    "swimming pool", "gym", "fitness center", "free wifi", "complimentary breakfast",
    "tripadvisor", "booking.com", "expedia", "agoda", "makemytrip", "oyo rooms",
    "restaurant", "dining", "dine in", "cuisine", "food menu", "dinner menu",
    "lunch menu", "breakfast menu", "our menu", "view menu", "order online",
    "make a reservation", "book a table", "table reservation", "reservations",
    "head chef", "executive chef", "culinary",
    "buffet", "a la carte", "fine dining", "rooftop dining", "outdoor seating",
    "takeaway", "takeout", "food delivery", "zomato", "swiggy",
    "cafe", "bistro", "bar & grill", "steakhouse", "seafood restaurant",
    "spa treatments", "massage therapy", "wellness center", "facial treatment",
    # Physical casino-RESORT vocabulary -- a brick-and-mortar "Hotel & Casino" whose
    # website is a lodging/dining/events brochure, not an online operator (FireKeepers,
    # Cherokee, Chinook Winds...). These co-occur with the hotel/dining terms above and
    # push such a page over the strong-hospitality bar so it can't be dragged to
    # "gambling" by bare "casino"/"slots" mentions on the same brochure.
    "hotel & casino", "hotel and casino", "casino resort", "casino & resort",
    "casino hotel", "players club", "plan your visit", "plan your stay", "book your stay",
    "gaming floor", "casino floor", "box office", "getting here", "directions & parking",
    "loyalty rewards", "stay & play", "meetings & events", "weddings & events",
}


# ── High-Conviction Domain Anchors ──────────────────────────────────────────
GAMBLING_TLDS = {".casino", ".bet", ".poker", ".bingo", ".lotto"}

GAMBLING_DOMAIN_KEYWORDS = {
    "bet", "bets", "betting", "casino", "casinos", "cazino", "poker", "slot", "slots",
    "satta", "matka", "roulette", "blackjack", "baccarat", "teenpatti", "andarbahar",
    "bookmaker", "sportsbook", "jackpot", "aviator", "rummy", "lottery", "lotto",
    "wagering", "dafabet", "1xbet", "1win", "mostbet", "melbet", "parimatch", "stake",
    "777", "888", "999", "bet365", "gambl", "wingo", "crazytime", "monopoly",
    "megaways", "jili", "spribe", "kingmaker", "bwin", "betway", "betfair",
    "spin", "spins", "winbuzz", "lotus365", "fairplay", "laser247", "cricbet99",
    "diamondexch", "reddyanna", "mahadevbook", "cricketid", "khelo", "pokies", "pokie",
    "daman", "baji", "tiranga", "goagames"
}

NON_GAMBLING_BET_WORDS = {
    "between", "better", "bethesda", "alphabet", "diabetes", "alphabetical",
    "tibetan", "elizabeth", "beta", "betty", "benefit", "beverage", "sorbet",
    "spinach", "sloth", "steakhouse", "lotus", "winter", "stakeholder",
    "pinwheel", "khelotennis", "777street", "window", "windows", "steak"
}

_TRADING_DOMAIN_WORDS = {
    "forex", "trade", "trading", "invest", "investing", "investment",
    "leverage", "broker", "brokerage", "market", "stock", "fund", "funds",
    "capital", "finance", "financial", "fx", "wealth", "asset", "assets",
}


def is_gambling_domain(url_or_domain: str) -> tuple[bool, str]:
    """
    Check if the domain or TLD itself is an unambiguous gambling domain with strict token boundaries.
    Returns (is_gambling, matched_signal).
    """
    if not url_or_domain:
        return False, ""

    clean = url_or_domain.lower()
    clean = re.sub(r"^https?://", "", clean).split("/")[0].split(":")[0].removeprefix("www.")

    # Check TLD
    for tld in GAMBLING_TLDS:
        if clean.endswith(tld):
            return True, f"gambling_tld({tld})"

    # Never anchor government, educational, banking, or calculator utility domains
    if any(clean.endswith(tld) for tld in (".gov", ".gov.in", ".nic.in", ".edu", ".ac.in", ".bank.in", ".bank", ".mil")):
        return False, ""
    if "calculator" in clean or "calculators" in clean:
        return False, ""

    domain_body = clean.split(".")[0]

    # Never anchor domains whose name clearly signals trading/financial services
    if any(tw in domain_body for tw in _TRADING_DOMAIN_WORDS):
        return False, ""

    # Check domain name tokens with hyphen/digit boundaries. The whitelist is applied
    # PER-TOKEN (not to the whole domain_body) — a domain_body-wide check meant a single
    # whitelisted false-positive word anywhere in the name (e.g. "window" in
    # "casino-window.com") silently masked a genuine gambling brand token ("casino") sitting
    # in a different token of the same domain, since the whole-string check short-circuited
    # before the token loop ever ran.
    # Split only on real word separators (hyphen/underscore/dot) — NOT digits. Splitting on
    # digits strips the leading "1" off "1xbet"/"1win" (both explicitly listed below), turning
    # them into "xbet"/"win" which then match nothing — live incident: "1xbet111.com" fell
    # through the domain-anchor check entirely and got decided by a single unchallenged AI
    # guess on near-empty page text instead. Substring matching below still isolates a real
    # brand token fine even when merged with adjacent digits (e.g. "bet365", "888casino").
    tokens = re.split(r"[-_.]+", domain_body)
    for token in tokens:
        if not token:
            continue
        # A token that IS (or contains) a known non-gambling false-positive word never
        # counts as a match — but only excludes THIS token, not sibling tokens.
        if any(ng in token for ng in NON_GAMBLING_BET_WORDS):
            continue
        for kw in GAMBLING_DOMAIN_KEYWORDS:
            # Require exact token match or clean subtoken boundary to prevent false positives like 'spinach'
            if kw == token or (len(kw) >= 4 and kw in token):
                return True, f"domain_keyword({kw})"

    return False, ""


def is_hospitality_site(text: str) -> tuple[bool, list[str]]:
    """Return (True, matched_signals) if page looks like a hotel / restaurant / dining venue."""
    text_lower = text.lower() if text else ""
    hits = [s for s in HOSPITALITY_OVERRIDE_SIGNALS if re.search(rf"\b{re.escape(s)}\b", text_lower)]
    return len(hits) >= 2, hits


# Editorial / blog / news article structure. A page ABOUT gambling (strategy blog, news
# story, wiki, glossary) is saturated with gambling vocabulary but is not an operator --
# it has an author, a publish date, and article furniture instead of a register/deposit
# funnel. Byline + date together is the low-noise combination; a real operator's homepage
# essentially never carries both.
_EDITORIAL_BYLINE_SIGNALS = (
    "posted by", "written by", "by admin", "author:", "editor:", "byline",
    "posted on", "published on", "published by", "last updated on", "updated on",
    "min read", "minute read", "reading time",
)
_EDITORIAL_FURNITURE_SIGNALS = (
    "read more", "continue reading", "read full article", "read full story",
    "leave a comment", "leave a reply", "post comment", "0 comments", "related posts",
    "related articles", "recent posts", "share this article", "share this post",
    "filed under", "tags:", "category:", "categories:", "table of contents",
    "in this article", "previous post", "next post", "about the author",
)


def looks_like_editorial(text: str) -> tuple[bool, list[str]]:
    """Return (True, hits) if the page reads as a blog post / news article / wiki entry
    rather than an operator site: a byline AND a date/read-time marker, or >= 3 pieces of
    article furniture."""
    t = text.lower() if text else ""
    byline = [s for s in _EDITORIAL_BYLINE_SIGNALS if s in t]
    furniture = [s for s in _EDITORIAL_FURNITURE_SIGNALS if s in t]
    has_byline = any(s in ("posted by", "written by", "by admin", "author:", "editor:", "byline", "about the author") for s in byline)
    has_datemark = any(s in ("posted on", "published on", "published by", "last updated on", "updated on", "min read", "minute read", "reading time") for s in byline)
    if (has_byline and has_datemark) or len(furniture) >= 3:
        return True, (byline + furniture)[:4]
    return False, []


def classify(html: str, keywords: set[str] | None = None, url: str | None = None, screenshot_input: str | bytes | None = None) -> tuple[str, list[str]]:
    """
    Weighted Keyword Threshold Pre-Classifier with Safety Gates, Domain Anchors & Targeted OCR:
    Returns: (decision, matched_keywords)
    Where decision is:
      - "gambling"  : Verified gambling domain anchor with live content, or >= 4.0 weighted score with strong signals
      - "needs_ai"  : Ambiguous sites, low score with gambling anchor, or hospitality needing review
      - "regular"   : Strictly non-gambling / confirmed negative archetype / < 1.5 score with no gambling anchor
    """
    if not html and not screenshot_input:
        return "regular", []

    # Institutional allowlist: bypass every downstream check entirely for domains you're
    # certain about (trusted_domains.json) — zero risk of a heuristic/AI mistake ever
    # touching your highest-consequence sites.
    if url and is_trusted_domain(url):
        return "regular", ["allowlist:trusted_domain"]

    # Hard exclusions for official government, academic, and banking TLDs
    if url:
        clean_host = url.lower().split("://")[-1].split("/")[0].split(":")[0].removeprefix("www.")
        if any(clean_host.endswith(tld) for tld in (".gov", ".gov.in", ".nic.in", ".edu", ".ac.in", ".bank.in", ".bank", ".mil")):
            return "regular", []

    text = _extract_text(html) if html else ""

    # Targeted OCR: If screenshot is provided, extract image banner text to catch stealth/poster gambling sites
    if screenshot_input:
        try:
            from checking_url.ocr_extractor import extract_ocr_text
            ocr_text = extract_ocr_text(screenshot_input)
            if ocr_text:
                text = f"{text} {ocr_text.lower()}"
        except Exception:
            pass

    # Check domain parking / for-sale markers first
    is_parked, parked_hits = is_parked_or_for_sale(text, html or "", url or "")
    if is_parked:
        return "regular", []

    # No-functional-interface gate: a page that is reachable but only shows a parked
    # lander, a regulatory/geo-block interstitial, a "coming soon" holder, or a server
    # error has no functional site behind it -- and therefore no functional gambling
    # interface to confirm. A casino/bet-named domain serving one of these is not an
    # operator we can verify, regardless of how the domain name reads or how many
    # gambling words the notice text contains. Runs before the domain-anchor and
    # keyword logic below so neither can override it.
    is_dead, dead_reason = is_dead_or_error_page(text)
    if is_dead:
        return "regular", [f"blocked:{dead_reason}"]

    # Check Domain Anchor early — cheap (no full ~995-keyword scan) — so a gambling-TLD
    # match can short-circuit BEFORE doing any of the expensive content analysis below.
    is_g_domain, domain_signal = is_gambling_domain(url) if url else (False, "")

    # Per explicit instruction: for gambling TLDs (.casino/.bet/.poker/.bingo/.lotto), skip
    # content classification entirely — liveness is the only bar. By this point the page has
    # already passed the parked/for-sale gate above and the trusted-allowlist / gov-edu-bank
    # TLD exclusions earlier in this function, so reaching here already means "reachable, not
    # parked, not an institutional domain" — sufficient to lock "gambling" regardless of page
    # content. Returning here also skips the ~995-keyword regex scan, 14-category negative-
    # archetype check, hospitality check, and actionable-signal check below entirely, since
    # none of that is needed (or used) for this decision — real, measurable cost at scale.
    if domain_signal.startswith("gambling_tld("):
        # Exception: physical casino RESORTS (Grey Eagle, Lac-Leamy, Little Creek, Great
        # Blue Heron...) and blog/news content also register .casino / .bet domains. A
        # strong lodging/dining/events brochure or an article structure on one of these
        # is not an online operator -- route to AI instead of an instant lock.
        _tld_hosp = len(is_hospitality_site(text)[1]) >= 3
        _tld_editorial = looks_like_editorial(text)[0]
        if _tld_hosp or _tld_editorial:
            return "needs_ai", [domain_signal, "physical_hospitality" if _tld_hosp else "editorial"]
        return "gambling", [domain_signal]

    kw_set = keywords if keywords is not None else load_keywords()

    # Match gambling keywords in visible text using strict word boundaries
    matched = [kw for kw in kw_set if re.search(rf"\b{re.escape(kw)}\b", text)]

    # Second pass over a de-obfuscated (leetspeak/letter-spacing-collapsed) copy of the
    # same text to catch keywords an attacker deliberately mangled to dodge the exact
    # scan above. Only ADDS matches on top of the plain-text pass above -- never
    # replaces it -- so this can only increase recall, never change how the plain-text
    # case behaves. Matches found only via de-obfuscation are worth knowing about
    # separately (an obfuscated gambling term is itself a meaningful evasion signal).
    deobf_variants = _deobfuscate_text(text)
    obfuscation_only_matched = [
        kw for kw in kw_set
        if kw not in matched and any(re.search(rf"\b{re.escape(kw)}\b", v) for v in deobf_variants)
    ]
    if obfuscation_only_matched:
        matched = matched + obfuscation_only_matched

    # Calculate weighted keyword score (weak signals count as 0.5, strong count as 1.0).
    # Bare category words (BARE_CATEGORY_SIGNALS) score 0 -- they still make has_strong_signals
    # True below (routing to needs_ai), but must never be able to stack toward score>=4.0's
    # instant-lock on their own; several of them together is exactly what a spam-injected
    # non-gambling site accumulates (see ibinfra.in incident, 2026-08-30).
    score = sum(
        0.0 if kw in BARE_CATEGORY_SIGNALS else (0.5 if kw in WEAK_GAMBLING_SIGNALS else 1.0)
        for kw in matched
    )

    # Check negative archetypes (e.g. pure math calculators, academic libraries, general e-commerce, banking, news, trading)
    is_neg, neg_reason = detect_negative_archetype(text)
    # A gambling review / comparison / affiliate site is saturated with operator vocabulary
    # BY DESIGN but is not itself an operator -- it sends visitors OUT to other brands to
    # register and wager. Tracked separately so it can be forced to "regular" (not just
    # "needs_ai") when the page has no real-money wagering CTA of its own.
    is_affiliate_review = "gambling_review_affiliate" in neg_reason

    # Check hospitality gate (hotel/resort/restaurant amenity pages)
    is_hosp, hosp_hits = is_hospitality_site(text)

    # Hard actionable signals: explicit online real-money deposit / cashout / betting engines.
    # Unambiguous phrases always count; generic banking/retail phrases (cashier, instant
    # withdrawal, deposit now...) only count if gambling-context vocabulary also appears —
    # otherwise a bank's ATM/net-banking page trips this on a single incidental word.
    has_hard_online_signals = any(
        re.search(rf"\b{re.escape(kw)}\b", text) for kw in ACTIONABLE_WAGERING_SIGNALS_UNAMBIGUOUS
    ) or (
        any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in ACTIONABLE_WAGERING_SIGNALS_GENERIC)
        and any(re.search(rf"\b{re.escape(c)}\b", text) for c in _ACTIONABLE_SIGNAL_CONTEXT)
    )
    has_strong_signals = any(kw in STRONG_GAMBLING_SIGNALS for kw in matched)
    # Same signal, but excluding bare category words -- required by the domain-anchor
    # branch below, whose own comment already says "only an unambiguous multi-word STRONG
    # signal is trustworthy enough to auto-lock without AI". A domain-anchor match (e.g.
    # "poker" in "pokerledger.net", "casino" in "casinobonuschecker.com") combined with
    # nothing but a bare "poker"/"casino" mention on the page was instant-locking straight
    # to "gambling" with zero AI review -- confirmed false positives on a poker bankroll
    # tracker and a casino review/affiliate site (2026-08-30, same incident as ibinfra.in).
    has_real_strong_signal = any(kw in STRONG_GAMBLING_SIGNALS and kw not in BARE_CATEGORY_SIGNALS for kw in matched)

    # If domain has an explicit gambling-KEYWORD anchor (e.g. "poker"/"bet"/"casino" in the
    # name, but not a gambling TLD — that case already returned above): a domain match alone
    # is NOT proof — GAMBLING_DOMAIN_KEYWORDS matches substrings (e.g. "stake"/"spin"/
    # "monopoly"), so it can hit unrelated brands/institutions. Only an unambiguous multi-word
    # STRONG signal on the live page is trustworthy enough to auto-lock without AI. Anything
    # murkier (hospitality wording, negative-archetype wording, weak-keyword-only, or zero
    # keywords on a JS shell) always goes to the AI for a second opinion instead of being
    # silently decided by heuristics alone in either direction.
    # Affiliate/review sites: force "regular" (not "needs_ai") when there's no own
    # real-money wagering CTA -- the sheer keyword density on these pages was getting them
    # AI-confirmed as gambling. Applies whether or not the domain name looks gambling
    # (many are e.g. "bestcasinos-review.com").
    if is_affiliate_review and not has_hard_online_signals:
        return "regular", matched

    # Blog post / news article / wiki ABOUT gambling: has an author + publish date +
    # article furniture instead of a register/deposit funnel. Force "regular" when there's
    # no real-money wagering CTA of its own -- same rationale as the affiliate case. Uses a
    # stricter "own funnel" test than has_hard_online_signals: descriptive mentions of game
    # NAMES ("aviator crash", "panel chart") don't count as this page's own mechanism.
    is_editorial, editorial_hits = looks_like_editorial(text)
    has_own_funnel = any(
        re.search(rf"\b{re.escape(kw)}\b", text)
        for kw in (ACTIONABLE_WAGERING_SIGNALS_UNAMBIGUOUS - _ACTIONABLE_GAME_NAME_PHRASES)
    ) or (
        any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in ACTIONABLE_WAGERING_SIGNALS_GENERIC)
        and any(re.search(rf"\b{re.escape(c)}\b", text) for c in _ACTIONABLE_SIGNAL_CONTEXT)
    )
    if is_editorial and not has_own_funnel:
        return ("regular", matched + [f"editorial:{editorial_hits[0]}"]) if editorial_hits else ("regular", matched)

    # Strong physical-hospitality signal (a brick-and-mortar hotel / resort / casino-resort
    # whose site is a lodging/dining/events brochure): >= 3 hospitality hits and no online
    # wagering CTA -> "regular" regardless of keyword score. Bare "casino"/"slots"/"jackpot"
    # mentions on a resort brochure must not drag it to "gambling".
    if len(hosp_hits) >= 3 and not has_hard_online_signals:
        return "regular", matched

    if is_g_domain:
        # Precision-first: a gambling-KEYWORD domain anchor is a substring match
        # (GAMBLING_DOMAIN_KEYWORDS hits "stake"/"spin"/"casino"/"bet" inside unrelated
        # names) and is NEVER sufficient on its own to auto-lock "gambling". Locking
        # without AI now requires BOTH an unambiguous multi-word strong signal AND an
        # actionable real-money wagering CTA present on the live page, and no negative
        # archetype. Everything else -- weak-keyword-only, strong-signal-but-no-CTA,
        # archetype wording, a JS shell -- goes to the AI.
        if has_real_strong_signal and has_hard_online_signals and not is_neg:
            return "gambling", matched or [domain_signal]
        return "needs_ai", (matched + [domain_signal]) if matched else [domain_signal]

    # Absolute Zero False-Positive Gate for Non-Anchored Domains:
    # If page is confirmed hotel/resort/dining OR a non-gambling archetype, has NO hard online
    # wagering proof, AND has essentially no gambling-keyword presence at all -> REGULAR.
    # Disguising a real betting site as a travel/hotel/hospitality front (or padding it with
    # negative-archetype boilerplate) is a known evasion tactic — requiring score to also be
    # near-zero means a site that scores even mildly on gambling keywords gets an AI look
    # instead of being dismissed on hospitality/archetype wording alone.
    if (is_hosp or is_neg) and not has_hard_online_signals and score < 1.5:
        return "regular", matched

    # Standard non-anchored domain logic:
    # 1. High Score (>= 4.0): Require at least ONE real (non-bare-category) strong or
    # actionable signal to auto-lock gambling without AI -- bare words already can't reach
    # 4.0 alone (scored 0), but a weak-signal-heavy page could theoretically stack there
    # too, so this uses has_real_strong_signal for the same reason the domain-anchor branch
    # does (see its comment).
    if score >= 4.0:
        if (is_hosp or is_neg) and not has_hard_online_signals:
            return "needs_ai", matched
        if has_real_strong_signal or has_hard_online_signals:
            return "gambling", matched
        # High score consisting purely of weak/bare-category terms -> route to AI for verification
        return "needs_ai", matched

    # 2. Any strong signal at all, regardless of total score: STRONG_GAMBLING_SIGNALS is a
    # tightly-curated, low-noise phrase list ("responsible gambling", "online casino", "sports
    # betting"...) specifically because those phrases almost never appear on a non-gambling
    # site. Previously this required score >= 2.0 in addition, meaning a real operator whose
    # visible text was thin (e.g. most of the marketing copy living in image alt text, or a
    # minimal single-page site) with exactly ONE strong-signal hit and nothing else could fall
    # through to instant "regular" with zero AI review despite unambiguous evidence — this is
    # what happened live with a genuine UK Gambling Commission-licensed operator whose only
    # scored hits were "responsible gambling" (strong) + "bet" (weak) = 1.5, just under the old
    # 2.0 floor. A lone strong signal is reason enough for an AI look by itself.
    if has_strong_signals:
        return "needs_ai", matched

    # 3. No strong signal and score too low to be meaningful: Strictly Regular Website
    return "regular", matched


# ── Precision self-check ────────────────────────────────────────────────────
# Run: python -m checking_url.classifier
# These page types were all being mislabelled "gambling" purely on a casino/bet-named
# domain (or sheer keyword density). Every one must resolve to "regular" with NO AI call.
# If a future edit to the gates regresses one of these, this fails loudly.
_SELFCHECK_CASES = [
    ("expired for-sale lander",
     "<h1>mrfortune-casino.net</h1><p>mrfortune-casino.net has expired and may be "
     "available at Dynadot auctions. This domain may be for sale.</p>",
     "https://mrfortune-casino.net", "regular"),
    ("parklogic parked page",
     "<h1>freshbetcasino.org</h1><ul><li>Style &amp; Fashion</li><li>Education</li>"
     "<li>Movies</li></ul><footer>&copy; 2026 ParkLogic.com. All rights reserved.</footer>",
     "https://freshbetcasino.org", "regular"),
    ("geo-block interstitial (Canada)",
     "<h1>Access to this website is restricted</h1><p>Under the Canadian Criminal Code, "
     "gambling and betting services must be offered by licensed entities. Access from "
     "Canada is blocked to remain compliant.</p>",
     "https://casinacho-casino.org", "regular"),
    ("geo-block interstitial (Poland)",
     "<h1>Strona jest niedostepna</h1><p>Zgodnie z ustawa o grach hazardowych, dostep "
     "do tej strony zostal ograniczony.</p>",
     "https://coolzinocasino.org", "regular"),
    ("gambling review / affiliate site",
     "<h1>Best Online Casinos 2026</h1><p>Read our review and editor rating of the top "
     "rated casinos. Affiliate disclosure: we may earn a commission. Compare casinos and "
     "compare bonuses below. Visit Casino to claim your welcome offer.</p>",
     "https://casino-reviews-guide.com", "regular"),
    ("blog post about gambling",
     "<article><h1>How the Aviator crash game multiplier actually works</h1>"
     "<p>Posted on March 3, 2026 by Rahul Sharma. 8 min read.</p><p>Aviator is a popular "
     "crash game. We break down the maths behind the online casino favourite.</p>"
     "<footer>Filed under: casino guides. Leave a comment. Related posts.</footer></article>",
     "https://gamblingtips-blog.com", "regular"),
    ("physical casino resort brochure",
     "<h1>Thunder Ridge Hotel &amp; Casino</h1><p>Plan your visit. Book your stay in a "
     "deluxe room. Fine dining, buffet, spa treatments, box office. Players club rewards. "
     "Directions &amp; parking. Weddings &amp; events. Our casino floor has slots and "
     "table games.</p>",
     "https://thunderridge-casino.com", "regular"),
    ("physical casino resort on .casino TLD",
     "<h1>Grey Eagle Resort &amp; Casino</h1><p>Plan your visit. Book your stay in our "
     "hotel. Fine dining, buffet, spa, box office live shows. Players club. Directions "
     "and parking. Meetings &amp; events.</p>",
     "https://grey-eagle.casino", "needs_ai"),
    # Real operators must still NOT be swallowed by the gates above.
    ("real operator (funnel present)",
     "<h1>Bet Big Casino</h1><p>online casino, live casino, sports betting. register and "
     "deposit. claim welcome bonus. place bet now. instant withdrawal.</p>",
     "https://betbig-casino.com", "gambling"),
    ("real operator on .casino TLD",
     "<h1>JW Casino</h1><p>Play online slots and live casino. Register, deposit, claim "
     "welcome bonus. Instant withdrawal.</p>",
     "https://jw.casino", "gambling"),
]


def _selfcheck() -> int:
    kw = load_keywords()
    bad = 0
    for name, html, url, expected in _SELFCHECK_CASES:
        decision, _ = classify(html, keywords=kw, url=url)
        ok = decision == expected
        bad += not ok
        print(f"  {'ok ' if ok else 'BAD'}  {name:34} -> {decision:9} (want {expected})")
    print("OK: all self-check cases pass" if not bad else f"FAIL: {bad} self-check case(s) regressed")
    return 1 if bad else 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_selfcheck())




