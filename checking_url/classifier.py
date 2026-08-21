"""
checking_url/classifier.py — High-Accuracy Heuristic Pre-Classifier.

Rules (Updated Architecture — Triple-Lock):
- Dead / Blocked -> handle upstream
- Parked / For-Sale landers -> send to AI with parked flag (no longer auto-reject)
- Negative Archetypes (Educational, E-Commerce, News/Wiki) -> auto-regular ONLY if >= 4 signals AND 0 keywords
- Score >= 5.0 (STRONG keywords weighted 2.0, WEAK 1.0) -> "gambling" (confirmed immediately, no AI needed)
- Score 2.5 to <5.0 -> "needs_ai" (escalated to Ollama AI Challenge Round)
- Score < 2.5 -> "regular" (no AI needed)
"""
import json
from pathlib import Path
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    "slot machines", "slot games", "video slots", "free spins", "claim free spins",
    "online lottery", "lottery result", "lottery prediction", "lucky draw winner",
    "betting id", "demo id", "whatsapp betting", "telegram betting",
    "wagering requirement", "wagering requirements", "bonus wagering",
    "cashback on losses", "deposit bonus", "no deposit bonus", "no-deposit bonus",
    "casino vip", "vip casino", "live baccarat", "live roulette", "live dealer",
    "crash game", "aviator game", "aviator betting", "aviator crash",
    "teen patti", "andar bahar", "jhandi munda", "dragon tiger", "responsible gambling",
    "wingogame", "wingo", "color prediction", "casino app", "betting app",
    "crypto casino", "bitcoin casino", "usdt betting",
    "prop bets", "parlay bet", "accumulator bet", "money line",
    "jodi chart", "panel chart", "half sangam", "full sangam",
    "win real money", "real cash games", "play for real money", "real money app",
}

# Actionable wagering signals — required to override confirmed news/editorial/regulatory sites
ACTIONABLE_WAGERING_SIGNALS = {
    "deposit money", "deposit now", "instant deposit", "deposit funds",
    "withdraw money", "instant withdrawal", "withdrawal request", "cashier",
    "play for real money", "win real money", "real cash games", "real money app",
    "claim bonus now", "claim welcome bonus", "wagering requirement", "bonus wagering",
    "betting id", "demo id", "whatsapp betting", "telegram betting", "place bet now",
    "aviator crash", "dragon tiger live", "teen patti live", "jodi chart", "panel chart"
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
        soup = BeautifulSoup(cleaned_raw, 'html.parser')
        parts = [soup.get_text(separator=' ')]
        title = soup.find('title')
        if title and title.string:
            parts.append(title.string)
        for meta in soup.find_all('meta', attrs={'name': True, 'content': True}):
            if meta['name'].lower() in ('description', 'keywords', 'og:description', 'og:title'):
                parts.append(meta['content'])
        return ' '.join(parts).lower()
    except Exception:
        # Fallback fast regex stripper if BeautifulSoup fails
        clean = re.sub(r'<[^>]+>', ' ', truncated_html)
        return re.sub(r'\s+', ' ', clean).lower()


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
    "commercial_banking": [
        "personal banking", "agri banking", "nri banking", "business banking",
        "savings account", "current account", "fixed deposit", "recurring deposit",
        "net banking", "netbanking", "mobile banking", "debit card", "credit card",
        "branch locator", "atm locator", "ifsc", "ifsc code", "rtgs", "neft", "imps",
        "rbi regulated", "interest rates", "loan against", "home loan", "car loan",
        "personal loan", "corporate banking", "wholesale banking", "rural banking",
        "deposit interest", "fixed deposits", "bank branch", "internet banking",
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
    "diamondexch", "reddyanna", "mahadevbook", "cricketid", "khelo"
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

    # Whitelist check for known non-gambling domains
    if any(ng in domain_body for ng in NON_GAMBLING_BET_WORDS):
        return False, ""

    # Never anchor domains whose name clearly signals trading/financial services
    if any(tw in domain_body for tw in _TRADING_DOMAIN_WORDS):
        return False, ""

    # Check domain name tokens with hyphen/digit boundaries
    tokens = re.split(r"[-_\d.]+", domain_body)
    for token in tokens:
        if not token:
            continue
        for kw in GAMBLING_DOMAIN_KEYWORDS:
            # Require exact token match or clean subtoken boundary to prevent false positives like 'spinach'
            if kw == token or (len(kw) >= 4 and kw in token and not any(ng in token for ng in NON_GAMBLING_BET_WORDS)):
                return True, f"domain_keyword({kw})"

    return False, ""


def is_hospitality_site(text: str) -> tuple[bool, list[str]]:
    """Return (True, matched_signals) if page looks like a hotel / restaurant / dining venue."""
    text_lower = text.lower() if text else ""
    hits = [s for s in HOSPITALITY_OVERRIDE_SIGNALS if re.search(rf"\b{re.escape(s)}\b", text_lower)]
    return len(hits) >= 2, hits


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

    kw_set = keywords if keywords is not None else load_keywords()

    # Check domain parking / for-sale markers first
    is_parked, parked_hits = is_parked_or_for_sale(text, html or "", url or "")
    if is_parked:
        return "regular", []

    # Match gambling keywords in visible text using strict word boundaries
    matched = [kw for kw in kw_set if re.search(rf"\b{re.escape(kw)}\b", text)]

    # Calculate weighted keyword score (weak signals count as 0.5, strong count as 1.0)
    score = sum(0.5 if kw in WEAK_GAMBLING_SIGNALS else 1.0 for kw in matched)

    # Check negative archetypes (e.g. pure math calculators, academic libraries, general e-commerce, banking, news, trading)
    is_neg, neg_reason = detect_negative_archetype(text)

    # Check hospitality gate (hotel/resort/restaurant amenity pages)
    is_hosp, hosp_hits = is_hospitality_site(text)

    # Hard actionable signals: explicit online real-money deposit / cashout / betting engines
    has_hard_online_signals = any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in ACTIONABLE_WAGERING_SIGNALS)
    has_strong_signals = any(kw in STRONG_GAMBLING_SIGNALS for kw in matched)

    # Check Domain Anchor first (.bet, .casino, or gambling keywords in domain)
    is_g_domain, domain_signal = is_gambling_domain(url) if url else (False, "")

    # If domain has an explicit gambling anchor (e.g. .bet, .casino, or "poker"/"bet" in name):
    if is_g_domain:
        if is_hosp and not has_hard_online_signals:
            return "regular", matched or [domain_signal]
        # Any keyword hit or strong gambling TLD -> confirmed gambling
        if matched or any(url.lower().endswith(t) for t in GAMBLING_TLDS):
            return "gambling", matched or [domain_signal]
        # If 0 keywords found in raw HTML (e.g. JS single-page app), send to AI review
        return "needs_ai", [domain_signal]

    # Absolute Zero False-Positive Gate for Non-Anchored Domains:
    # If page is confirmed hotel/resort/dining OR a non-gambling archetype and has NO hard online wagering proof -> REGULAR
    if (is_hosp or is_neg) and not has_hard_online_signals:
        return "regular", matched

    # Standard non-anchored domain logic:
    # 1. High Score (>= 4.0): Require at least ONE strong or actionable signal to auto-lock gambling without AI
    if score >= 4.0:
        if (is_hosp or is_neg) and not has_hard_online_signals:
            return "regular", matched
        if has_strong_signals or has_hard_online_signals:
            return "gambling", matched
        # High score consisting purely of weak/promotional terms -> route to AI for verification
        return "needs_ai", matched

    # 2. Medium Score (>= 2.0) with at least 1 strong signal:
    if score >= 2.0 and has_strong_signals:
        return "needs_ai", matched

    # 3. Low Score (< 2.0 or no strong signal): Strictly Regular Website
    return "regular", matched




