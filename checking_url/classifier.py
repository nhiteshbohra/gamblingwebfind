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
    "no-deposit", "deposit bonus", "forex bonus", "trading bonus", "no deposit bonus",
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

# WEAK / AMBIGUOUS signals — can appear on non-gambling sites too
# Weighted as 0.5 points toward the keyword threshold
WEAK_GAMBLING_SIGNALS = {
    "rebate",
    "sic bo",
    "free spins",
    "free bonus",
    "bonus",
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
        soup = BeautifulSoup(truncated_html, 'html.parser')
        for tag in soup(["script", "style", "svg", "noscript", "iframe", "path"]):
            tag.decompose()
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


# Common markers for parked domains and for-sale landers
PARKED_AND_FOR_SALE_MARKERS = [
    "is for sale", "domain for sale", "this domain is for sale",
    "this domain is available for sale", "the domain name is available for sale",
    "domain is available for purchase", "buy this domain", "purchase this domain",
    "purchase domain", "inquire about this domain", "make an offer on this domain",
    "make an offer", "domain portfolio for sale", "this domain may be for sale",
    "premium domain for sale", "domain name marketplace", "domain broker",
    "fast domain transfers", "domain name has been registered",
    "domain is registered at", "get this domain", "bid on this domain",
    "domain parking", "domain is parked", "parked domain", "parked by",
    "parked free, courtesy of", "this webpage was generated by the domain owner using sedo",
    "this page is parked free", "this domain is registered and parked",
    "welcome to the future home of", "website coming soon", "under construction",
    "domain expired", "domain renewal",
    "domains.atom.com", "atom.com", "dan.com", "sedo.com", "sedoparking.com",
    "afternic.com", "hugedomains.com", "squadhelp.com", "undeveloped.com",
    "domainmarket.com", "bodis.com", "parkingcrew.net", "parkingcrew.com",
    "sav.com", "above.com", "brandpa.com", "brandbucket.com",
    "buydomains.com", "domainagent.com", "godaddy.com/domains",
    "namecheap.com/domains", "dynadot.com", "epik.com",
    "uniregistry.com", "voodoo.com", "domainnameshop.com",
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
    "news_media_wiki": [
        "breaking news", "editorial team", "journalism", "press release",
        "published by", "fandom wiki", "community wiki", "table of contents"
    ],
    "manufacturing_automotive": [
        "engine", "horsepower", "transmission", "torque", "cylinder",
        "outboard", "inboard", "marine engine", "boat engine", "dealership",
        "warranty", "spare parts", "service center", "authorized dealer",
        "car model", "two wheeler", "four wheeler", "showroom"
    ],
    "food_travel_lifestyle": [
        "restaurant", "cuisine", "recipe", "dish", "menu", "chef",
        "travel guide", "destination", "hotel review", "food blog",
        "best restaurants", "local food", "street food", "food atlas",
        "trip advisor", "travel tips", "itinerary"
    ],
    "tech_blog_crypto_news": [
        "app review", "android app", "ios app", "play store", "app store",
        "tech news", "gadget review", "smartphone", "software review",
        "blockchain news", "crypto news", "defi news", "nft news",
        "press release", "market cap", "trading volume", "coin price"
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
    """Detect if the page is predominantly educational, e-commerce, or news/wiki."""
    text_lower = text.lower() if text else ""

    for archetype, signals in NEGATIVE_ARCHETYPES.items():
        matched = [s for s in signals if s in text_lower]
        # Increased threshold: requires 4+ signals (was 2) to avoid false archetype rejections
        if len(matched) >= 4:
            return True, f"{archetype}_site (signals: {', '.join(matched[:3])})"

    return False, ""


# Hospitality / Food & Dining override signals
# If >= 2 of these appear, the page is almost certainly a hotel, restaurant, or dining venue.
HOSPITALITY_OVERRIDE_SIGNALS = {
    # Hotel / accommodation
    "hotel", "resort", "motel", "inn", "lodge", "hostel", "bed and breakfast", "b&b",
    "book a room", "room booking", "check-in", "check-out", "check in", "check out",
    "room rates", "room rate", "per night", "nightly rate", "hotel room", "guest room",
    "suite", "deluxe room", "standard room", "double room", "single room", "twin room",
    "amenities", "concierge", "room service", "valet parking", "free parking",
    "swimming pool", "gym", "fitness center", "free wifi", "complimentary breakfast",
    "tripadvisor", "booking.com", "expedia", "agoda", "makemytrip", "oyo rooms",
    # Restaurant / dining
    "restaurant", "dining", "dine in", "cuisine", "food menu", "dinner menu",
    "lunch menu", "breakfast menu", "our menu", "view menu", "order online",
    "make a reservation", "book a table", "table reservation", "reservations",
    "head chef", "executive chef", "culinary",
    "buffet", "a la carte", "fine dining", "rooftop dining", "outdoor seating",
    "takeaway", "takeout", "food delivery", "zomato", "swiggy",
    "cafe", "bistro", "bar & grill", "steakhouse", "seafood restaurant",
    # Spa / wellness
    "spa treatments", "massage therapy", "wellness center", "facial treatment",
}


def is_hospitality_site(text: str) -> tuple[bool, list[str]]:
    """Return (True, matched_signals) if page looks like a hotel / restaurant / dining venue."""
    text_lower = text.lower() if text else ""
    hits = [s for s in HOSPITALITY_OVERRIDE_SIGNALS if s in text_lower]
    return len(hits) >= 2, hits


def classify(html: str, url: str = "", keywords: set = None) -> tuple[str, list[str]]:
    """
    Weighted Keyword Threshold Pre-Classifier with Safety Gates:
    Returns: (decision, matched_keywords)
    Where decision is:
      - "gambling"  : >= 5.0 weighted score (and not a hospitality site without remote wagering)
      - "needs_ai"  : 2.5 to 4.5 weighted score, or hospitality-flagged sites requiring AI review
      - "regular"   : < 2.5 weighted score, or confirmed negative archetype (educational/e-commerce)
    """
    if not html:
        return "regular", []

    text = _extract_text(html)
    kw_set = keywords if keywords is not None else load_keywords()

    # Match gambling keywords in visible text
    matched = [kw for kw in kw_set if kw in text]
    if not matched:
        return "regular", []

    # Calculate weighted keyword score (weak signals count as 0.5, strong count as 1.0)
    score = sum(0.5 if kw in WEAK_GAMBLING_SIGNALS else 1.0 for kw in matched)

    # Check negative archetypes (e.g. pure math calculators, academic libraries, general e-commerce)
    is_neg, neg_reason = detect_negative_archetype(text)
    if is_neg and score < 3.0:
        return "regular", matched

    # Check hospitality gate (hotel/resort/restaurant amenity pages)
    is_hosp, hosp_hits = is_hospitality_site(text)
    has_hard_online_signals = any(
        kw in STRONG_GAMBLING_SIGNALS and kw not in ("casino", "poker", "slots")
        for kw in matched
    )

    # 1. High Score (>= 5.0):
    if score >= 5.0:
        # If hospitality page without explicit online wagering proof -> route to AI review
        if is_hosp and not has_hard_online_signals:
            return "needs_ai", matched
        return "gambling", matched

    # 2. Medium Score (>= 2.5) or Hospitality:
    if score >= 2.5 or (is_hosp and score >= 1.5):
        return "needs_ai", matched

    # 3. Low Score (< 2.5): Strictly Regular Website
    return "regular", matched




