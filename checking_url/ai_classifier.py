"""
checking_url/ai_classifier.py — OmniRoute AI Dual-Model Classifier.

Architecture: Analyst + Validator (Judge) Pattern powered by OmniRoute AI Gateway
- Round 1 (Analyst): Standard deep semantic classification with Indian domain rules
- Round 2 (Analyst): If Round 1 says "regular" → evidence challenge
- Validator: If analyst says "gambling" → skeptic validator challenges the verdict,
  checks if cited evidence is real, catches false positives
- Tiebreaker: Decisive final arbitrator for disputed verdicts
"""
import os
import subprocess
import sys
import time
import json
import re
import asyncio
import base64
import logging
import warnings
from collections import deque
from pathlib import Path
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
import aiohttp
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# Load root .env
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("ai_classifier")

# Import keyword strength tiers and domain anchor helpers from classifier
try:
    from checking_url.classifier import (
        STRONG_GAMBLING_SIGNALS,
        is_hospitality_site,
        GAMBLING_TLDS,
        GAMBLING_DOMAIN_KEYWORDS,
        NON_GAMBLING_BET_WORDS,
        _TRADING_DOMAIN_WORDS,
        is_gambling_domain,
    )
except ImportError:
    STRONG_GAMBLING_SIGNALS = {
        "online casino", "sports betting", "sportsbook", "betting exchange",
        "live casino", "casino games", "online gambling", "slot machines",
        "satta matka", "online poker", "online lottery",
        "responsible gambling", "teen patti", "andar bahar", "dragon tiger",
        "crypto casino", "bitcoin casino", "aviator game", "crash game",
    }
    GAMBLING_TLDS = {".casino", ".bet", ".poker", ".bingo", ".lotto"}
    GAMBLING_DOMAIN_KEYWORDS = {
        "bet", "bets", "betting", "casino", "casinos", "cazino", "poker", "slot", "slots",
        "satta", "matka", "roulette", "blackjack", "baccarat", "teenpatti", "andarbahar",
        "bookmaker", "sportsbook", "jackpot", "aviator", "rummy", "lottery", "lotto",
        "wagering", "dafabet", "1xbet", "1win", "mostbet", "melbet", "parimatch", "stake",
        "777", "888", "999", "bet365", "gambl"
    }
    NON_GAMBLING_BET_WORDS = {"between", "better", "bethesda", "alphabet", "diabetes", "alphabetical"}
    _TRADING_DOMAIN_WORDS = {"forex", "trade", "trading", "invest", "broker", "fund", "fx"}
    def is_hospitality_site(text: str):
        return False, []
    def is_gambling_domain(url: str):
        return False, ""

# ── Configuration (OmniRoute AI Gateway) ─────────────────────────────────────
OMNIROUTE_BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1").rstrip("/")
OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", "")
OMNIROUTE_MODEL = os.getenv("OMNIROUTE_MODEL", "auto")
OMNIROUTE_VALIDATOR_MODEL = os.getenv("OMNIROUTE_VALIDATOR_MODEL", "auto")
OMNIROUTE_TIEBREAKER_MODEL = os.getenv("OMNIROUTE_TIEBREAKER_MODEL", "auto")
OMNIROUTE_VISION_MODEL = os.getenv("OMNIROUTE_VISION_MODEL", "auto")
AI_CONCURRENCY = int(os.getenv("AI_CONCURRENCY", 8))
USE_CRAWL4AI_FOR_AI = os.getenv("USE_CRAWL4AI_FOR_AI", "false").lower() == "true"
ENABLE_VISION_AI = os.getenv("ENABLE_VISION_AI", "false").lower() == "true"

# Dynamic timeout bounds (seconds)
AI_TIMEOUT_MIN = float(os.getenv("AI_TIMEOUT_MIN", 10.0))   # fastest simple pages
AI_TIMEOUT_MAX = float(os.getenv("AI_TIMEOUT_MAX", 60.0))   # slowest complex pages
AI_TIMEOUT_BASE = float(os.getenv("AI_TIMEOUT_BASE", 25.0)) # initial seed before data

# ── Analyst & Validator System Instructions ──────────────────────────────────
ANALYST_SYSTEM_PROMPT = """You are an expert Cyber Crime Intelligence Analyst specializing in detecting illegal online gambling, real-money gaming, betting exchanges, Satta networks, online lotteries, and gambling-adjacent platforms operating in India and globally.

Your sole task is to analyze extracted website text, HTML metadata, page titles, and visual text to determine whether the website is a GAMBLING/BETTING platform or a REGULAR non-gambling website.

1. CRITICAL POSITIVE INDICATORS (Classify as "gambling" if ANY are present):
A. Cricket & Sports Betting Exchanges:
   - IPL, T20, Test, World Cup, Tennis, Football, Kabaddi betting or live odds.
   - Betting exchange terms: "Back / Lay", "Bookmaker", "Fancy Odds", "Session / Khai-Lagai", "Match Odds", "Punter", "Exchange 247".
   - Betting syndicates / Clone platforms: Mahadev Book, Reddy Anna, Laser247, Fairplay, Betbhai9, Cricbet99, Lotus365, SkyExchange, Diamondexch, Winbuzz.
   - Funnels: "Get Instant Demo ID", "WhatsApp for Betting ID", "Telegram Bookie ID", "Customer Care WhatsApp".

B. Casino & Table Games:
   - Traditional Indian Games: Teen Patti, Andar Bahar, Jhandi Munda, 7 Up Down, Mang Patta, Real-money Rummy / Points Rummy.
   - Live Dealer / International Casino: Roulette, Blackjack, Baccarat, Dragon Tiger, Sic Bo, Live Casino streaming.
   - Slots & Crash Games: Aviator, Crash X, Spaceman, JetX, Rocket game, Mines, Plinko, Megaways, Spin & Win, Wheel of Fortune.
   - Casino resorts: classified as gambling ONLY if the site itself advertises its gaming floor, slots, or table games for wagering.

C. Lottery & Prize Draw Platforms:
   - Any online lottery site, state lottery result portal, or lottery prediction site (e.g. bhagyashree lottery, nagaland lottery, kerala lottery, sikkim state lottery).
   - Scratch cards, instant win games, lucky draw, prize draw competitions.
   - Kalyan Matka, Mumbai Main, Gali Disawar, Milan Day/Night, Rajdhani, Satta King.

D. Financial & Promotional Triggers:
   - Deposit/Withdrawal: UPI deposit, Paytm/PhonePe/GPay instant deposit, IMPS, Crypto/USDT betting, "Instant 5-minute withdrawal", "Minimum deposit ₹100".
   - Bonuses: "100% First Deposit Bonus", "₹500 Signup Bonus", "Free Spins", "200x Multiplier", "Daily Cashback on Losses".
   - APK Distribution: "Download Betting App APK", "Install Casino Android App", "Play Real Money App".

2. FALSE POSITIVE GUARDRAILS (Classify as "regular" ONLY with specific evidence):
Do NOT classify as gambling if you can provide SPECIFIC page evidence that the website is:
- Educational / Academic: College, University, School portals.
- Genuine E-Commerce: Shopping cart, product listings with prices, physical shipping.
- News / Wiki / Government: News reporting on raids, Wikipedia history, Government anti-gambling warnings.
- Hospitality (Hotels/Resorts/Restaurants): Room bookings, menus, conference spaces without remote gambling.
- Commercial Banking & Financial Institutions: Official banks, credit unions, NBFCs (savings accounts, loans, fixed deposits, net banking). NEVER classify an official bank as gambling simply because it mentions deposits or accounts.
- Review / Ranking / Comparison / Affiliate Sites: A site that reviews, ranks, or compares casinos/betting sites with outlinks to third-party brands and NO Login/Register of its own is REGULAR (media/affiliate). Classify as "gambling" if the site has its OWN Login/Register account system for this brand to place bets or play games.

3. RESOLUTION POLICY:
- If a site has real-money cash stakes, betting IDs, live casino games, Satta Matka, or lottery → "gambling".
- When in doubt and cannot cite a specific gambling mechanism → classify as "regular".

Respond ONLY with a valid JSON object:
{
  "verdict": "gambling" or "regular",
  "confidence": <float 0.0-1.0>,
  "category": "<sports_betting | live_casino | satta_matka | crash_game | lottery | casino_resort | betting_id_funnel | regular>",
  "key_triggers": ["<specific signals on this page>"],
  "reason": "<one sentence explanation>"
}
"""

VALIDATOR_SYSTEM_PROMPT = """You are an expert Cyber Crime Intelligence Validator.
Another analyst model has evaluated a website and suspected it of being "gambling".
Your job is to double-check the verdict skeptically against the actual page content.

CONFIRM GAMBLING ("validation": "confirmed", "verdict": "gambling"):
- Casino/Card games: Roulette, Blackjack, Baccarat, Teen Patti, Andar Bahar, Poker, Slots, Aviator crash game.
- Sports/Cricket Betting: Live IPL odds, Match odds, Bookmaker, Betting IDs, Exchange.
- Real-money deposit/withdrawal flows: UPI, Paytm, crypto betting, Demo IDs.
- Satta Matka or Lotteries: Kalyan Matka, Satta King, Nagaland/Kerala lotteries.
- The page IS the operator: has its OWN Login/Register account system for this brand, or states it is a licensed gambling operator.

REJECT GAMBLING ("validation": "rejected", "verdict": "regular"):
- Hospitality / Dining: Genuine hotel, resort, restaurant with no gambling floor.
- News / Educational / Legal: News articles, legal analysis, university portals.
- E-Commerce: Sells physical goods with a shopping cart.
- Official Bank / Financial Portal: Savings, loans, fixed deposits, net banking.
- Review / Ranking / Affiliate: Compares casinos and links OUT to third-party brands; no operator login/deposit of its own.

Respond ONLY in valid JSON:
If confirmed gambling:
{
  "verdict": "gambling",
  "confidence": <float 0.0-1.0>,
  "validation": "confirmed",
  "rejection_reason": null,
  "evidence_check": "<quote the exact gambling text from page>",
  "final_reason": "<one sentence>"
}
If rejected:
{
  "verdict": "regular",
  "confidence": <float 0.0-1.0>,
  "validation": "rejected",
  "rejection_reason": "<why prior verdict was rejected>",
  "evidence_check": "<quote non-gambling text from page>",
  "final_reason": "<one sentence>"
}
"""


class DynamicTimeoutManager:
    """
    Adaptive timeout calculator for AI requests.
    Tracks a rolling Exponential Moving Average (EMA) of actual response times.
    """
    EMA_ALPHA = 0.25        # EMA smoothing factor
    SAFETY_MULTIPLIER = 1.6 # timeout = ema_time * 1.6
    CHARS_PER_SECOND = 150  # approx model chars-per-second baseline

    def __init__(self):
        self._ema_response_time: float = AI_TIMEOUT_BASE
        self._total_calls: int = 0
        self._total_timeouts: int = 0
        self._lock = asyncio.Lock()
        self._recent_outcomes: deque = deque(maxlen=10)

    def compute_timeout(self, prompt_chars: int) -> float:
        content_factor = max(0.0, (prompt_chars - 500) / self.CHARS_PER_SECOND)
        raw = self._ema_response_time * self.SAFETY_MULTIPLIER + content_factor
        clamped = max(AI_TIMEOUT_MIN, min(AI_TIMEOUT_MAX, raw))
        return round(clamped, 1)

    async def record_success(self, elapsed: float):
        async with self._lock:
            self._total_calls += 1
            self._recent_outcomes.append(False)
            self._ema_response_time = (
                self.EMA_ALPHA * elapsed
                + (1 - self.EMA_ALPHA) * self._ema_response_time
            )

    async def record_timeout(self):
        async with self._lock:
            self._total_calls += 1
            self._total_timeouts += 1
            self._recent_outcomes.append(True)
            self._ema_response_time = min(
                AI_TIMEOUT_MAX / self.SAFETY_MULTIPLIER,
                self._ema_response_time * 1.05
            )

    def recent_timeout_rate(self) -> float:
        if not self._recent_outcomes:
            return 0.0
        return sum(self._recent_outcomes) / len(self._recent_outcomes)

    async def reset_ema(self):
        async with self._lock:
            self._ema_response_time = AI_TIMEOUT_BASE
            self._total_calls = 0
            self._total_timeouts = 0
            self._recent_outcomes.clear()

    def stats(self) -> str:
        timeout_rate = (
            f"{self._total_timeouts}/{self._total_calls}"
            f" ({100*self._total_timeouts/max(1,self._total_calls):.0f}%)"
        )
        return (
            f"EMA={self._ema_response_time:.1f}s | "
            f"Timeouts={timeout_rate} | "
            f"Current window=[{AI_TIMEOUT_MIN}s–{AI_TIMEOUT_MAX}s]"
        )


# Global dynamic timeout manager
_timeout_mgr = DynamicTimeoutManager()

# Known iGaming game provider CDNs / APIs / iframes
IGAMING_PROVIDERS = (
    "pragmaticplay", "evolutiongaming", "evolution.com", "spribe", "jiligaming", "ezugi",
    "sexybaccarat", "supernowa", "kingmaker", "habanero", "playngo", "pgsoft",
    "microgaming", "netent", "relax-gaming", "betgames.tv", "fastspin", "cq9gaming",
    "yggdrasil", "redtiger", "bgaming", "playtech", "betsoft", "endorphina",
    "wazdan", "spinomenal", "booming-games", "evoplay", "smartsoft", "nolimitcity",
    "thunderkick", "blueprintgaming", "amatic", "gamomat", "greentube", "novomatic",
    "jili", "sexygaming", "fachai", "dreamgaming", "sa gaming", "wm casino", "allbet"
)

# High-conviction deposit/cashier/VIP funnel hooks
GAMBLING_FUNNEL_MARKERS_STRONG = (
    "get demo id", "create master id", "whatsapp betting", "telegram betting",
    "instant deposit", "instant withdrawal", "24/7 withdrawal", "fast payout",
    "usdt deposit", "trc20 deposit", "crypto cashier", "betting exchange id"
)

GAMBLING_FUNNEL_MARKERS_SOCIAL = (
    "wa.me/", "api.whatsapp.com/send", "t.me/", "telegram.me/",
)

_FUNNEL_SOCIAL_CONTEXT = (
    "bet", "betting", "casino", "satta", "matka", "odds", "deposit", "withdraw",
    "demo id", "bookmaker", "cricket id", "ipl", "live odds", "bonus",
)


def detect_gambling_funnels(html: str) -> list[str]:
    """Detect high-conviction WhatsApp/Telegram betting funnels and cashier hooks."""
    if not html:
        return []
    html_lower = html.lower()

    matched = [m for m in GAMBLING_FUNNEL_MARKERS_STRONG if m in html_lower]
    has_gambling_context = any(re.search(rf"\b{re.escape(c)}\b", html_lower) for c in _FUNNEL_SOCIAL_CONTEXT)
    if has_gambling_context:
        matched += [m for m in GAMBLING_FUNNEL_MARKERS_SOCIAL if m in html_lower]

    return matched


LICENSED_OPERATOR_SIGNALS = (
    "licensed and regulated by", "licensed and regulated in", "licensed by the gambling commission",
    "gambling commission account", "under account", "malta gaming authority",
    "curacao egaming", "curacao gaming license", "regulated by the gambling commission",
    "our gambling license", "gaming license number",
)


def detect_licensed_operator_signals(text: str) -> list[str]:
    """Detect verbatim regulator/self-exclusion-body mentions in page text."""
    if not text:
        return []
    text_lower = text.lower()
    return [s for s in LICENSED_OPERATOR_SIGNALS if s in text_lower]


_VALIDATOR_OPERATOR_TELLS = (
    "reputable online casino", "an online casino", "is a casino", "online gambling platform",
    "is a gambling site", "which is a gambling site", "is a gambling platform", "a gambling site,",
    "sports betting platform", "sports betting site", "is a bookmaker", "is a betting platform",
    "describes 1xbet", "describes 1win", "1xbet", "1win", "melbet", "parimatch", "dafabet",
    "pg88", "nohu", "slot gacor", "poker slot machines", "symbols on reels", "crash game",
    "casino brand", "aviator game", "teen patti", "satta matka", "sportsbook", "toto site",
    "judol", "gambling regulator", "gambling commission", "gaming authority",
    "gaming licence", "gaming license", "malta gaming", "curacao",
)
_VALIDATOR_NEGATION_RE = re.compile(r"(?:\bnot\b|n't|\bno\b|resembl|domain name only|only the name)\s+(?:\w+\s+){0,3}$")


def validator_reject_self_contradicts(val_result: dict) -> list[str]:
    """Detect operator language in validator's own rejection text."""
    blob = " ".join(str(val_result.get(k, "") or "") for k in
                     ("rejection_reason", "final_reason", "evidence_check")).lower()
    if not blob:
        return []
    hits = []
    for tell in _VALIDATOR_OPERATOR_TELLS:
        i = blob.find(tell)
        if i == -1:
            continue
        if _VALIDATOR_NEGATION_RE.search(blob[max(0, i - 40):i]):
            continue
        hits.append(tell)
    return hits


_GAMBLING_SPECIFIC_CTA_MARKERS = (
    "claim bonus", "bet id", "demo id", "bookmaker", "spin", "play now",
    "lottery", "lotto", "scratch", "no-deposit", "free bonus",
)


def detect_operator_cta_signals(cta_buttons: list | None) -> list[str]:
    """Detect gambling-specific interactive CTAs."""
    if not cta_buttons:
        return []
    hits = []
    for btn in cta_buttons:
        btn_lower = str(btn).lower()
        for marker in _GAMBLING_SPECIFIC_CTA_MARKERS:
            if marker in btn_lower:
                hits.append(str(btn))
                break
    return hits


def detect_igaming_providers(html: str) -> list[str]:
    """Detect if HTML loads assets from known casino game provider CDNs."""
    if not html:
        return []
    src_values = " ".join(re.findall(r"""(?:data-)?src\s*=\s*["']([^"']{5,300})["']""", html, re.IGNORECASE)).lower()
    return [p for p in IGAMING_PROVIDERS if p in src_values]


REGULAR_CONVICTION_THRESHOLD = 0.50

NON_GAMBLING_EVIDENCE_MAP = {
    "hotel_hospitality": [
        "hotel", "resort", "book a room", "hotel reservation", "check-in", "check-out",
        "guest rooms", "hotel suites", "deluxe room", "amenities", "concierge",
        "spa & wellness", "fine dining", "restaurant", "menu", "banquet",
        "conference rooms", "weddings & events", "resort fee", "pool & cabanas",
        "valet parking", "directions", "tripadvisor", "room service", "stay with us"
    ],
    "parked_domain": [
        "parked free", "courtesy of godaddy", "get this domain", "this domain is for sale",
        "buy this domain", "domain for sale", "domain is parked", "sedo.com",
        "hugedomains", "afternic", "dan.com", "parkingcrew", "bodis", "related search topics"
    ],
    "access_denied_error": [
        "access denied", "you don't have permission to access", "403 forbidden",
        "error 403", "errors.edgesuite.net", "reference #", "security service"
    ],
    "calculator": ["calculator", "calculate", "tax calculator", "gst calculator", "emi calculator", "loan calculator", "percentage calculator", "age calculator", "bmi calculator", "calorie calculator", "currency converter", "unit converter", "salary calculator"],
    "university": ["university", "campus", "tuition", "faculty", "degree", "curriculum", "admissions", "alumni"],
    "hospital": ["hospital", "patient", "clinic", "doctor", "physician", "medical center", "healthcare"],
    "government": ["ministry", "official portal", "government of", "department of", "citizen", "public notice"],
    "ecommerce": ["add to cart", "shopping cart", "shipping policy", "product review", "return policy", "order tracking"],
    "news": ["breaking news", "journalism", "editorial board", "reuters", "associated press", "published on"],
    "saas_enterprise_tech": ["customer service", "help desk", "sdk", "api integration", "pricing plans", "book a demo", "free trial", "enterprise", "saas", "software", "cloud platform", "developer documentation", "support ticket", "customer support"],
    "fintech_investing_wealth": [
        "mutual funds", "portfolio management", "wealth management", "stock market",
        "brokerage", "etf", "asset management", "invest online", "trading account",
        "demat account", "financial advisor", "sebi registered", "mas regulated",
        "sec registered", "robo advisor", "sip", "investment",
        "forex", "foreign exchange", "cfds", "cfd trading", "pip", "spread betting",
        "lot size", "leverage", "margin call", "stop loss", "take profit",
        "mt4", "mt5", "metatrader", "fca regulated", "asic regulated", "cysec",
        "regulated broker", "technical analysis", "fundamental analysis",
        "candlestick", "risk management", "demo account", "live account",
        "open an account", "trading platform", "equity trading", "share market",
    ],
    "entertainment_media_cinema": ["movie review", "box office", "celebrity gossip", "cinema news", "film review", "trailer", "ott release", "streaming guide", "entertainment news", "bollywood", "hollywood", "actor", "actress", "tv shows", "cinema"],
    "sports_scores_stats": ["live cricket score", "ball by ball commentary", "ipl score", "match schedule", "point table", "player stats", "match scorecard", "fixtures", "team standings", "scorecard", "live score"],
    "commercial_banking": [
        "personal banking", "agri banking", "nri banking", "business banking",
        "savings account", "current account", "fixed deposit", "recurring deposit",
        "net banking", "netbanking", "mobile banking", "debit card", "credit card",
        "branch locator", "atm locator", "ifsc", "rtgs", "neft", "imps",
        "rbi regulated", "interest rates", "home loan", "car loan", "personal loan",
        "deposit interest", "fixed deposits", "bank branch", "internet banking",
        "karnataka bank", "state bank", "hdfc", "icici", "axis bank", "punjab national",
        "bank of baroda", "canara bank", "union bank", "indian bank", "commercial bank",
    ],
}

# Global session and semaphore
_session: aiohttp.ClientSession | None = None
_session_loop = None
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _semaphore is None or _semaphore_loop != current_loop:
        _semaphore = asyncio.Semaphore(AI_CONCURRENCY)
        _semaphore_loop = current_loop
    return _semaphore


async def get_session() -> aiohttp.ClientSession:
    global _session, _session_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _session is None or _session.closed or _session_loop != current_loop:
        if _session and not _session.closed:
            try:
                await _session.close()
            except Exception:
                pass
        connector = aiohttp.TCPConnector(limit=50, keepalive_timeout=60)
        timeout = aiohttp.ClientTimeout(total=AI_TIMEOUT_MAX)
        _session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        _session_loop = current_loop
    return _session


def clean_page_text(html: str, max_chars: int = 2500) -> tuple[str, str, list[str], str]:
    """
    DOM Stripper Engine:
    Extracts Title, Meta Description, interactive CTAs, and cleaned body text.
    """
    if not html:
        return "", "", [], ""

    truncated_html = html[:350000]
    if "<" not in truncated_html:
        return "", "", [], truncated_html[:max_chars].strip()

    try:
        soup = BeautifulSoup(truncated_html, "html.parser")

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        meta_desc = ""
        for meta in soup.find_all("meta", attrs={"name": True, "content": True}):
            if meta["name"].lower() in ("description", "keywords", "og:description", "og:title"):
                meta_desc += " " + meta["content"].strip()
        meta_desc = meta_desc.strip()

        cta_buttons = []
        cta_patterns = (
            "whatsapp", "wa.me", "telegram", "t.me", "deposit", "withdraw",
            "login", "register", "signup", "apk", "download", "bet id",
            "demo id", "bookmaker", "odds", "claim bonus", "spin", "play now",
            "lottery", "lotto", "scratch", "no-deposit", "free bonus", "prize",
        )
        for tag in soup.find_all(["a", "button"]):
            href = str(tag.get("href", "")).lower()
            text = tag.get_text(strip=True).lower()
            combined = f"{text} {href}"
            if any(p in combined for p in cta_patterns):
                clean_btn = re.sub(r"\s+", " ", tag.get_text(strip=True))[:50]
                if clean_btn and clean_btn not in cta_buttons:
                    cta_buttons.append(clean_btn)
                if len(cta_buttons) >= 8:
                    break

        alt_texts = [img["alt"].strip() for img in soup.find_all("img", attrs={"alt": True}) if img["alt"].strip()]

        for tag in soup(["script", "style", "svg", "noscript", "iframe", "path"]):
            tag.decompose()

        body_text = soup.get_text(separator=" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)
        if alt_texts:
            body_text = (body_text + " " + " ".join(alt_texts)).strip()

        trimmed_text = body_text[:max_chars].strip()
        return title, meta_desc, cta_buttons, trimmed_text
    except Exception:
        clean = re.sub(r'<[^>]+>', ' ', truncated_html)
        trimmed = re.sub(r'\s+', ' ', clean)[:max_chars].strip()
        return "", "", [], trimmed


async def extract_markdown_crawl4ai(url: str = None, html: str = None, max_chars: int = 3000) -> str | None:
    """Optional Stage 2 Crawl4AI Extraction Engine."""
    if not USE_CRAWL4AI_FOR_AI:
        return None
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
        async with AsyncWebCrawler(verbose=False) as crawler:
            if url:
                res = await crawler.arun(
                    url=url,
                    config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
                )
            elif html:
                res = await crawler.arun(
                    url="raw:" + html[:150000],
                    config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
                )
            else:
                return None
            
            md = getattr(res, "markdown", None) or getattr(res, "cleaned_html", "")
            if md and isinstance(md, str) and len(md.strip()) > 80:
                return md[:max_chars].strip()
    except Exception:
        return None


async def prepare_page_context_for_ai(html: str, url: str = None, max_chars: int = 2500) -> tuple[str, str, list[str], str]:
    """Unified AI Context Preparer."""
    title, meta_desc, cta_buttons, body_text = clean_page_text(html, max_chars=max_chars)
    if USE_CRAWL4AI_FOR_AI:
        try:
            md_text = await extract_markdown_crawl4ai(url=url, html=html, max_chars=max_chars)
            if md_text and len(md_text.strip()) > 100:
                body_text = md_text
        except Exception:
            pass

    return title, meta_desc, cta_buttons, body_text


def parse_ai_json_response(raw_text: str) -> dict | None:
    """Safely extract and parse JSON object from LLM generation."""
    if not raw_text:
        return None

    try:
        data = json.loads(raw_text.strip())
        if isinstance(data, dict) and "verdict" in data:
            return data
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw_text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "verdict" in data:
                return data
        except Exception:
            pass

    return None


def _evaluate_regular_evidence(reason: str, evidence: str, body_text: str, html: str) -> tuple[bool, str]:
    """Anti-Hallucination Ground-Truth Evaluator."""
    combined_ai_claim = (reason + " " + evidence).lower()
    page_content = (body_text + " " + (html or "")[:100000]).lower()

    has_gambling_terms = any(re.search(rf"\b{re.escape(kw)}\b", page_content) for kw in (
        "bet", "betting", "casino", "satta", "matka", "poker", "slot", "slots",
        "odds", "bookmaker", "sportsbook", "wagering", "aviator", "roulette"
    ))

    verified_archetypes = []
    for archetype, patterns in NON_GAMBLING_EVIDENCE_MAP.items():
        if has_gambling_terms and archetype in ("news", "sports_scores_stats"):
            continue

        claimed = any(p in combined_ai_claim for p in patterns)
        if claimed:
            actual_matches = [p for p in patterns if p in page_content]
            if actual_matches:
                verified_archetypes.append(f"{archetype}({','.join(actual_matches[:2])})")

    if len(verified_archetypes) >= 2:
        return True, f"Verified real non-gambling evidence in HTML: {', '.join(verified_archetypes)}"

    if len(verified_archetypes) >= 1:
        return True, f"Verified non-gambling evidence: {verified_archetypes[0]}"

    return False, "AI claimed non-gambling features that do not exist in page HTML (hallucination rejected)"


# ── Translation circuit breaker ──────────────────────────────────────────────
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "").strip()
TRANSLATE_TIMEOUT = float(os.getenv("TRANSLATE_TIMEOUT", 30.0))
TRANSLATE_WINDOW = int(os.getenv("TRANSLATE_WINDOW", 6))
TRANSLATE_MIN_SAMPLE = int(os.getenv("TRANSLATE_MIN_SAMPLE", 4))
TRANSLATE_FAIL_RATE = float(os.getenv("TRANSLATE_FAIL_RATE", 0.75))
_translate_outcomes: deque[bool] = deque(maxlen=TRANSLATE_WINDOW)
_translate_attempts = 0
_translate_disabled = False


async def translate_preflight() -> bool:
    """Probe at run start to verify translation is usable."""
    global _translate_disabled
    if not TRANSLATE_MODEL:
        _translate_disabled = True
        return False
    try:
        messages = [
            {"role": "system", "content": "You are a translator. Output only the translation."},
            {"role": "user", "content": "Translate to English: hola mundo"}
        ]
        res = await _call_omniroute(messages, model=TRANSLATE_MODEL, timeout_override=10.0)
        if res:
            logger.info(f"[translate] preflight OK ({TRANSLATE_MODEL})")
            return True
    except Exception as e:
        logger.warning(f"[translate] preflight failed ({e}) -- translation OFF for this run.")
    _translate_disabled = True
    return False


def _translate_note_result(ok: bool):
    """Update the rate-based circuit breaker."""
    global _translate_attempts, _translate_disabled
    _translate_attempts += 1
    _translate_outcomes.append(not ok)
    if _translate_disabled or _translate_attempts < TRANSLATE_MIN_SAMPLE:
        return
    if len(_translate_outcomes) >= min(TRANSLATE_WINDOW, TRANSLATE_MIN_SAMPLE) and \
       (sum(_translate_outcomes) / len(_translate_outcomes)) >= TRANSLATE_FAIL_RATE:
        _translate_disabled = True
        logger.warning(
            "[translate] disabled for the rest of this run -- %d/%d recent attempts failed.",
            sum(_translate_outcomes), len(_translate_outcomes),
        )


async def translate_to_english_if_needed(html: str, url: str = "") -> tuple[str, bool]:
    """Detect a non-English page and translate visible text to English using OmniRoute."""
    if not html or not TRANSLATE_MODEL or _translate_disabled:
        return html, False
    from checking_url.classifier import _extract_text
    text = _extract_text(html)
    if len(text) < 200:
        return html, False
    try:
        from langdetect import detect
        lang = detect(text[:2000])
    except Exception:
        return html, False
    if lang == "en":
        return html, False

    messages = [
        {"role": "system", "content": "You are a translator. Translate the following webpage text into English. Output ONLY the translated text with no commentary, no markdown, no notes."},
        {"role": "user", "content": f"Translate to English:\n\n{text[:1200]}"}
    ]
    sem = get_semaphore()
    async with sem:
        if _translate_disabled:
            return html, False
        try:
            session = await get_session()
            headers = {"Content-Type": "application/json"}
            if OMNIROUTE_API_KEY:
                headers["Authorization"] = f"Bearer {OMNIROUTE_API_KEY}"
            payload = {
                "model": TRANSLATE_MODEL,
                "messages": messages,
                "temperature": 0.1,
            }
            async with session.post(
                f"{OMNIROUTE_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=TRANSLATE_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    choices = result.get("choices", [])
                    if choices:
                        translated = (choices[0].get("message", {}).get("content") or "").strip()
                        if translated:
                            _translate_note_result(True)
                            logger.info(f"[translate] {url} | {lang}->en | {len(text)}->{len(translated)} chars")
                            return translated, True
                _translate_note_result(False)
                logger.warning(f"[translate] HTTP {resp.status} for {url} (lang={lang}), model={TRANSLATE_MODEL}")
                return html, False
        except Exception as e:
            _translate_note_result(False)
            logger.warning(f"[translate] Failed for {url} (lang={lang}): {type(e).__name__}: {e}")
    return html, False


async def _call_omniroute(
    messages: list[dict],
    model: str = None,
    url: str = "",
    timeout_override: float = None,
    _retry_on_offline: bool = True,
) -> dict | None:
    """
    Make an OpenAI-compatible Chat Completion call to the OmniRoute gateway.
    Supports dynamic timeout, retry safety, and robust JSON output parsing.
    """
    import time

    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
    dynamic_timeout = timeout_override or _timeout_mgr.compute_timeout(prompt_chars)
    target_model = model or OMNIROUTE_MODEL

    payload = {
        "model": target_model,
        "messages": messages,
        "temperature": 0.1,
    }

    headers = {
        "Content-Type": "application/json",
    }
    if OMNIROUTE_API_KEY:
        headers["Authorization"] = f"Bearer {OMNIROUTE_API_KEY}"

    sem = get_semaphore()
    async with sem:
        t_start = time.monotonic()
        try:
            session = await get_session()
            async with session.post(
                f"{OMNIROUTE_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=dynamic_timeout),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    elapsed = time.monotonic() - t_start
                    await _timeout_mgr.record_success(elapsed)
                    logger.debug(
                        f"[ai_classifier] {url} | chars={prompt_chars} | "
                        f"timeout_given={dynamic_timeout:.1f}s | "
                        f"actual={elapsed:.1f}s | model={target_model}"
                    )
                    choices = result.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        return parse_ai_json_response(content)
                    return None
                elif resp.status in (401, 403):
                    logger.error(f"[ai_classifier] OmniRoute auth error HTTP {resp.status} - check OMNIROUTE_API_KEY")
                elif resp.status == 502:
                    logger.warning(f"[ai_classifier] OmniRoute Bad Gateway (502) for {url} using model {target_model}")
                else:
                    err_text = await resp.text()
                    logger.warning(f"[ai_classifier] OmniRoute HTTP {resp.status} for {url}: {err_text[:200]}")
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError):
            if _retry_on_offline:
                logger.info(f"[ai_classifier] OmniRoute gateway is offline at {OMNIROUTE_BASE_URL}. Attempting auto-start...")
                if await start_omniroute_if_needed():
                    logger.info(f"[ai_classifier] OmniRoute started successfully. Retrying request for {url}...")
                    return await _call_omniroute(
                        messages=messages,
                        model=model,
                        url=url,
                        timeout_override=timeout_override,
                        _retry_on_offline=False,
                    )
            logger.warning(f"[ai_classifier] OmniRoute gateway is offline at {OMNIROUTE_BASE_URL} for {url}")
        except asyncio.TimeoutError:
            await _timeout_mgr.record_timeout()
            logger.warning(
                f"[ai_classifier] Timeout after {dynamic_timeout:.1f}s for {url} "
                f"| chars={prompt_chars} | mgr: {_timeout_mgr.stats()}"
            )
        except Exception as e:
            logger.warning(f"[ai_classifier] OmniRoute inference failed for {url}: {e}")
    return None


async def _call_vision_model(image_path: str, prompt: str, log_ctx: str = "") -> dict | None:
    """
    OmniRoute vision-model call: reads the screenshot and formats it as an OpenAI
    Call a vision-capable model using the standard OpenAI chat completions
    image_url data URI sent to the OmniRoute gateway.
    """
    if not ENABLE_VISION_AI or not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.warning(f"[vision] Could not read screenshot {image_path}: {e}")
        return None

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }
    ]

    return await _call_omniroute(
        messages,
        model=OMNIROUTE_VISION_MODEL,
        url=log_ctx,
        timeout_override=max(30.0, AI_TIMEOUT_MAX),
    )


async def classify_screenshot_vision(image_path: str, url: str = "") -> dict | None:
    """Vision-model last resort for canvas/WebGL-rendered gambling UIs."""
    prompt = f"""You are looking at a screenshot of the website {url}.

Does this image show an online casino, sports betting, slot machine, live dealer game, poker
table, betting odds / bet-slip interface, or lottery / crash-game UI?

Look specifically for: slot machine reels, casino chips or playing cards, a roulette wheel,
a bet-amount input with "Bet"/"Spin"/"Deposit" buttons, sports odds tables, a live dealer
video feed, jackpot/prize displays, or crash-game multiplier graphics (e.g. Aviator).

A generic business/app homepage, login screen, article, or product page with none of these
elements is NOT gambling, even if the color scheme is flashy.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular",
  "confidence": 0.0 to 1.0,
  "visual_evidence": "what you actually see in the image that supports this"
}}
"""
    return await _call_vision_model(image_path, prompt, log_ctx=url)


_start_lock = asyncio.Lock()
_omniroute_proc = None


async def check_omniroute_status() -> tuple[bool, str]:
    """Check if OmniRoute gateway is accessible and authenticated."""
    try:
        session = await get_session()
        headers = {"Authorization": f"Bearer {OMNIROUTE_API_KEY}"} if OMNIROUTE_API_KEY else {}
        async with session.get(f"{OMNIROUTE_BASE_URL}/models", headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
            if resp.status == 200:
                data = await resp.json()
                models = data.get("data", [])
                return True, f"OmniRoute Gateway active ({len(models)} models available, default='{OMNIROUTE_MODEL}')"
            elif resp.status in (401, 403):
                return False, f"OmniRoute auth failed (HTTP {resp.status}) — check OMNIROUTE_API_KEY"
            return False, f"OmniRoute returned HTTP status {resp.status}"
    except Exception as e:
        return False, f"Cannot connect to OmniRoute at {OMNIROUTE_BASE_URL}: {e}"


async def start_omniroute_if_needed(timeout_sec: float = 25.0) -> bool:
    """
    Check if OmniRoute gateway is running. If not, auto-launch 'omniroute serve'
    in the background and wait until it responds to health checks.
    """
    global _omniroute_proc
    ok, _ = await check_omniroute_status()
    if ok:
        return True

    async with _start_lock:
        # Re-check status inside the lock to avoid double-launch
        ok, _ = await check_omniroute_status()
        if ok:
            return True

        print(f"[+] OmniRoute is offline at {OMNIROUTE_BASE_URL}. Auto-starting OmniRoute server...")
        try:
            if sys.platform == "win32":
                # Windows: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
                creationflags = 0x00000008 | 0x00000200
                _omniroute_proc = subprocess.Popen(
                    "omniroute serve",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            else:
                _omniroute_proc = subprocess.Popen(
                    ["omniroute", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception as err:
            try:
                # Fallback to npx if direct CLI command is not resolved
                if sys.platform == "win32":
                    _omniroute_proc = subprocess.Popen(
                        "npx omniroute serve",
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x00000008 | 0x00000200,
                    )
                else:
                    _omniroute_proc = subprocess.Popen(
                        ["npx", "omniroute", "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
            except Exception as err2:
                logger.error(f"[ai_classifier] Failed to auto-launch OmniRoute: {err2}")
                return False

        t_end = time.monotonic() + timeout_sec
        while time.monotonic() < t_end:
            await asyncio.sleep(1.0)
            ok, msg = await check_omniroute_status()
            if ok:
                print(f"[+] OmniRoute server started successfully! ({msg})")
                return True

        logger.warning(f"[ai_classifier] OmniRoute process started but did not respond within {timeout_sec}s")
        return False


async def ensure_omniroute_ready() -> bool:
    """Verify OmniRoute gateway is online, auto-starting it if currently offline."""
    ok, msg = await check_omniroute_status()
    if ok:
        print(f"[+] AI Gateway: {msg}")
        return True
    # Auto-start if offline
    if await start_omniroute_if_needed():
        ok, msg = await check_omniroute_status()
        if ok:
            print(f"[+] AI Gateway: {msg}")
            return True
    print(f"[!] AI Gateway: Could not connect or auto-start OmniRoute on {OMNIROUTE_BASE_URL}")
    print(f"    Please verify OmniRoute installation or run 'omniroute serve' manually.")
    return False


async def close_ai_session():
    """Close HTTP session."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def _call_tiebreaker(url: str, title: str, body_text: str, analyst_verdict: dict, dispute_reason: str) -> dict | None:
    """Third opinion from tiebreaker model for genuinely disputed cases."""
    key_triggers = analyst_verdict.get("key_triggers", [])
    prompt = f"""You are the final decisive reviewer. Two prior AI passes could not agree on
this website. Make a clear, final call.

URL: {url}
Title: {title}
Page Content: {body_text[:2500]}

First model said: verdict={analyst_verdict.get('verdict')}, confidence={analyst_verdict.get('confidence')}, reason="{analyst_verdict.get('reason', '')}"
Second model (validator) could not decide: "{dispute_reason}"

Rules:
- Legitimate banks, financial institutions, hotels/restaurants, educational portals, government
  sites, and e-commerce stores are REGULAR — reject gambling even if isolated words like
  'deposit', 'bonus', 'win', or 'stake' appear in a non-gambling context.
- Real-money online casinos, sportsbooks, poker/rummy/teen patti platforms, betting exchanges,
  crash games (Aviator), and lottery/satta-matka sites are GAMBLING.
- A site that REVIEWS, RANKS, or COMPARES gambling operators with outlinks and NO Login/Register of its OWN is REGULAR.
- You must pick "gambling" or "regular" — do not say unconfirmed/uncertain.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular",
  "confidence": 0.0 to 1.0,
  "reason": "final decisive reason"
}}
"""
    tb_messages = [
        {"role": "system", "content": "You are a cyber intelligence referee making a decisive final ruling on gambling websites. Respond strictly in valid JSON."},
        {"role": "user", "content": prompt}
    ]
    try:
        tb = await _call_omniroute(
            tb_messages,
            model=OMNIROUTE_TIEBREAKER_MODEL,
            url=url,
            timeout_override=max(45.0, AI_TIMEOUT_MAX * 1.5)
        )
        if tb and str(tb.get("verdict", "")).strip().lower() in ("gambling", "regular"):
            logger.info(f"[tiebreaker] Resolved disputed verdict for {url}: {tb.get('verdict')} ({dispute_reason})")
            return {
                "verdict": str(tb.get("verdict")).strip().lower(),
                "confidence": float(tb.get("confidence", 0.6)),
                "category": "tiebreaker_resolved",
                "key_triggers": key_triggers,
                "reason": f"Tie-breaker resolved dispute: {tb.get('reason', '')}",
                "challenge_override": True,
            }
    except Exception as e:
        logger.warning(f"[tiebreaker] Failed for {url}: {type(e).__name__}: {e}")
    return None


async def validate_gambling_verdict(
    url: str,
    title: str,
    body_text: str,
    analyst_verdict: dict,
    cta_buttons: list | None = None,
) -> dict:
    """Validator (Judge) Model — challenges a gambling verdict from the analyst."""
    key_triggers = analyst_verdict.get("key_triggers", [])
    analyst_reason = analyst_verdict.get("reason", "")
    analyst_confidence = analyst_verdict.get("confidence", 0.5)
    licensed_operator_signals = detect_licensed_operator_signals(body_text)
    operator_cta_signals = detect_operator_cta_signals(cta_buttons)

    if licensed_operator_signals and not operator_cta_signals:
        licensed_operator_signals = []
    hard_evidence_signals = licensed_operator_signals + operator_cta_signals

    validator_prompt = f"""URL: {url}
Title: {title}
Page Content: {body_text[:2000]}

--- PREVIOUS MODEL VERDICT ---
Verdict: gambling
Confidence: {analyst_confidence}
Triggers cited: {', '.join(str(t) for t in key_triggers[:5])}
Reason given: {analyst_reason}

Your task: Validate whether this gambling verdict is CORRECT based on the page content above.
Is the cited evidence ACTUALLY present in the page text? Is this truly an online gambling/betting site?

CRITICAL FALSE-POSITIVE CHECKS:
- If this is a commercial bank, financial institution, hotel/restaurant, educational portal, government site, or physical e-commerce store, you MUST REJECT the gambling verdict and return "verdict": "regular", "validation": "rejected".
- Do not confirm gambling solely because words like 'deposit', 'bonus', 'rewards', or 'win' appear in a banking, corporate, or retail context.

CRITICAL FALSE-NEGATIVE CHECK (a real licensed operator must NOT be rejected):
- If the page states it is "licensed and regulated" by a gambling regulator, cites a gambling license/account number, links to self-exclusion or problem-gambling support bodies (GamStop, GamCare, BeGambleAware), or says something like "you can register and place a bet" — confirm "gambling".
- The bet-slip or game grid is very often hidden behind login — do not require it to be visible before confirming gambling.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular" or "unconfirmed",
  "confidence": 0.0 to 1.0,
  "validation": "confirmed" or "rejected" or "uncertain",
  "rejection_reason": null or "specific reason the prior verdict was wrong",
  "evidence_check": "quote from page text that confirms or refutes the trigger",
  "final_reason": "one sentence final decision reason"
}}
"""

    val_messages = [
        {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
        {"role": "user", "content": validator_prompt},
    ]
    try:
        val_result = await _call_omniroute(
            val_messages,
            model=OMNIROUTE_VALIDATOR_MODEL,
            url=url,
            timeout_override=max(35.0, _timeout_mgr.compute_timeout(len(validator_prompt)) * 1.5),
        )
        if val_result is not None:
            val_verdict = str(val_result.get("verdict", "gambling")).strip().lower()
            val_validation = str(val_result.get("validation", "confirmed")).strip().lower()
            rejection_reason = val_result.get("rejection_reason")

            _self_contra = validator_reject_self_contradicts(val_result)
            _hard = list(hard_evidence_signals) + _self_contra

            if val_validation == "rejected" and val_verdict == "regular" and not _hard:
                logger.info(f"[validator] REJECTED gambling for {url}: {rejection_reason}")
                return {
                    "verdict": val_verdict,
                    "confidence": val_result.get("confidence", 0.4),
                    "category": "validator_override",
                    "key_triggers": key_triggers,
                    "reason": f"Validator rejected: {rejection_reason or val_result.get('final_reason', '')}",
                    "challenge_override": True,
                }
            elif val_validation == "rejected" and val_verdict == "regular" and _hard:
                dispute_reason = (
                    f"Validator rejected despite operator evidence "
                    f"({'page: ' + ', '.join(hard_evidence_signals[:3]) if hard_evidence_signals else ''}"
                    f"{'; ' if hard_evidence_signals and _self_contra else ''}"
                    f"{'validator self-contradiction: ' + ', '.join(_self_contra[:3]) if _self_contra else ''}) "
                    f"(validator said: {rejection_reason or val_result.get('final_reason', '')})"
                )
                tb_result = await _call_tiebreaker(url, title, body_text, analyst_verdict, dispute_reason)
                if tb_result is not None:
                    return tb_result
                return {
                    "verdict": "unconfirmed",
                    "confidence": 0.3,
                    "category": "validator_uncertain",
                    "key_triggers": key_triggers,
                    "reason": f"Disputed: {dispute_reason} (tie-breaker also unavailable)",
                    "challenge_override": False,
                }
            elif val_validation == "rejected" and val_verdict != "gambling":
                dispute_reason = rejection_reason or val_result.get('final_reason', '') or f"validator rejected but returned verdict={val_verdict!r}"
                if hard_evidence_signals:
                    dispute_reason += f" (hard evidence signals present: {', '.join(hard_evidence_signals[:4])})"
                tb_result = await _call_tiebreaker(url, title, body_text, analyst_verdict, dispute_reason)
                if tb_result is not None:
                    return tb_result
                return {
                    "verdict": "unconfirmed",
                    "confidence": 0.3,
                    "category": "validator_uncertain",
                    "key_triggers": key_triggers,
                    "reason": f"Validator disputed but did not confirm: {dispute_reason} (tie-breaker also unavailable)",
                    "challenge_override": False,
                }
            elif val_validation == "uncertain":
                dispute_reason = val_result.get('final_reason', '') or "validator uncertain"
                if hard_evidence_signals:
                    dispute_reason += f" (hard evidence signals present: {', '.join(hard_evidence_signals[:4])})"
                tb_result = await _call_tiebreaker(url, title, body_text, analyst_verdict, dispute_reason)
                if tb_result is not None:
                    return tb_result
                return {
                    "verdict": "unconfirmed",
                    "confidence": 0.4,
                    "category": "validator_uncertain",
                    "key_triggers": key_triggers,
                    "reason": f"Validator uncertain: {dispute_reason} (tie-breaker also unavailable)",
                    "challenge_override": False,
                }
            else:
                confirmed_result = dict(analyst_verdict)
                confirmed_result["reason"] = (
                    f"{analyst_reason} [Validated: {val_result.get('evidence_check', '')[:100]}]"
                )
                return confirmed_result
    except Exception as e:
        logger.warning(
            f"[validator] Validation failed for {url}: {type(e).__name__}: {e or 'no error message'}"
        )

    logger.warning(
        f"[validator] Validator model unavailable for {url} — trying tie-breaker "
        f"before falling back to 'unconfirmed' "
        f"(Analyst said '{analyst_verdict.get('verdict')}', "
        f"confidence={analyst_verdict.get('confidence')})"
    )
    tb_result = await _call_tiebreaker(url, title, body_text, analyst_verdict, "validator model unavailable")
    if tb_result is not None:
        return tb_result
    return {
        "verdict": "unconfirmed",
        "confidence": 0.0,
        "category": "validator_unavailable",
        "key_triggers": analyst_verdict.get("key_triggers", []),
        "reason": (
            f"Unconfirmed: Validator model unavailable, Analyst said "
            f"'{analyst_verdict.get('verdict')}' but could not be independently "
            f"confirmed — requeued for re-check"
        ),
        "challenge_override": False,
    }


async def classify_with_challenge(
    html: str,
    url: str = "",
    matched_keywords: list = None,
    fast_mode: bool = False,
) -> dict:
    """Anti-Hallucination 2-Round AI Challenge Classifier with Domain Anchors."""
    # ── 0. Pre-Flight Deterministic Checks ────────────────────────────────────
    is_g_domain, domain_signal = is_gambling_domain(url)
    detected_providers = detect_igaming_providers(html)
    detected_funnels = detect_gambling_funnels(html)

    # 100% Proof: Known iGaming provider CDNs/API iframes in HTML
    if detected_providers:
        return {
            "verdict": "gambling",
            "confidence": 0.99,
            "category": "online_casino",
            "key_triggers": [f"provider:{p}" for p in detected_providers] + (matched_keywords or []),
            "reason": f"Active iGaming game provider CDN/assets detected: {', '.join(detected_providers)}",
            "challenge_override": False,
        }

    # 98% Proof: High-conviction cashier / Telegram / WhatsApp betting funnel with matched keywords
    if detected_funnels and (matched_keywords or is_g_domain):
        return {
            "verdict": "gambling",
            "confidence": 0.98,
            "category": "betting_funnel",
            "key_triggers": [f"funnel:{f}" for f in detected_funnels] + (matched_keywords or []),
            "reason": f"Active betting transaction funnel detected: {', '.join(detected_funnels[:2])}",
            "challenge_override": False,
        }

    # Safe trusted TLDs: .bank.in, .bank, .gov, .gov.in, .nic.in, .edu, .ac.in, .mil
    clean_host = re.sub(r"^https?://", "", (url or "").lower()).split("/")[0].split(":")[0].removeprefix("www.")
    if any(clean_host.endswith(tld) for tld in (".gov", ".gov.in", ".nic.in", ".edu", ".ac.in", ".bank.in", ".bank", ".mil")):
        if not detected_providers and not detected_funnels:
            return {
                "verdict": "regular",
                "confidence": 0.99,
                "category": "official_trusted_tld",
                "key_triggers": [],
                "reason": f"Official verified banking/government/educational TLD ({clean_host})",
                "challenge_override": False,
            }

    title, meta_desc, cta_buttons, body_text = await prepare_page_context_for_ai(html, url=url, max_chars=2500)
    cta_str = ", ".join(cta_buttons) if cta_buttons else "None"

    # ── ROUND 1: Standard Classification ──────────────────────────────────────
    round1_prompt = f"""URL: {url}
Title: {title}
Meta Description: {meta_desc}
Action Buttons / Links: {cta_str}
Page Content:
{body_text}

Analyze the above website content carefully.
Determine if this is an online gambling, sports betting, real-money gaming (poker/rummy/teen patti), crash game (Aviator), lottery, or bookmaker platform.
IMPORTANT RULES:
- Real-money poker platforms, betting exchanges, sportsbooks, slot games, and rummy/card game platforms for cash are GAMBLING.
- A hotel/resort/dining website that merely mentions a casino, gaming floor, or amenity nearby is REGULAR unless the site itself provides remote/online gambling.
- Official commercial banks, financial institutions, government websites, educational institutions, or utility calculators are STRICTLY REGULAR.
- Do NOT hallucinate features. Only evaluate what is present in the text above.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular",
  "confidence": 0.0 to 1.0,
  "category": "category_name",
  "key_triggers": ["keywords or features found"],
  "reason": "concise explanation"
}}
"""

    r1_messages = [
        {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
        {"role": "user", "content": round1_prompt},
    ]
    round1 = await _call_omniroute(r1_messages, model=OMNIROUTE_MODEL, url=url)

    if round1 is None:
        if is_g_domain:
            return {
                "verdict": "unconfirmed",
                "confidence": 0.0,
                "category": "unconfirmed",
                "key_triggers": [domain_signal],
                "reason": f"ai_timeout_domain_anchor_present({domain_signal})_requeue",
                "challenge_override": False,
            }
        strong_matches = [k for k in (matched_keywords or []) if k in STRONG_GAMBLING_SIGNALS]
        if strong_matches:
            return {
                "verdict": "unconfirmed",
                "confidence": 0.0,
                "category": "unconfirmed",
                "key_triggers": strong_matches,
                "reason": f"ai_timeout_strong_keywords_present: {', '.join(strong_matches[:3])}_requeue",
                "challenge_override": False,
            }
        return {
            "verdict": "unconfirmed",
            "confidence": 0.0,
            "category": "unconfirmed",
            "key_triggers": [],
            "reason": "ai_timeout_no_signals",
            "challenge_override": False,
        }

    verdict = str(round1.get("verdict", "")).strip().lower()
    confidence = float(round1.get("confidence", 0.5))

    # Gambling verdict -> send to Validator Model for skeptical cross-examination
    if verdict == "gambling":
        triggers = round1.get("key_triggers", [])
        if is_g_domain:
            triggers.append(domain_signal)
        analyst_verdict = {
            "verdict": "gambling",
            "confidence": confidence,
            "category": str(round1.get("category", "gambling")),
            "key_triggers": triggers,
            "reason": str(round1.get("reason", "AI Round 1 gambling classification")),
            "challenge_override": False,
        }
        return await validate_gambling_verdict(url, title, body_text, analyst_verdict, cta_buttons)

    # ── ROUND 2: Challenge — AI must prove "regular" verdict with Ground Truth ──
    if verdict == "regular":
        is_hosp, _ = is_hospitality_site(body_text)
        has_providers = bool(detect_igaming_providers(html))
        has_funnels = bool(detect_gambling_funnels(html))

        _strong_kw = [k for k in (matched_keywords or []) if k in STRONG_GAMBLING_SIGNALS]
        _operator_ctas = detect_operator_cta_signals(cta_buttons)
        has_gambling_corroboration = bool(
            is_g_domain or _strong_kw or _operator_ctas or has_providers or has_funnels
        )

        if is_g_domain:
            _is_parked = any(m in body_text.lower() for m in [
                "is for sale", "domain for sale", "buy this domain", "parked by",
                "sedo.com", "dan.com", "godaddy.com", "hugedomains", "parkingcrew",
                "domain parking", "parked domain", "domain is for sale",
            ])
            _is_blocked_page = any(m in (title + " " + body_text).lower() for m in [
                "403 forbidden", "access denied", "you have been blocked",
                "error 403", "cloudflare", "just a moment",
            ])
            if _is_parked:
                return {"verdict": "dead", "confidence": 0.95, "category": "parked_domain",
                        "key_triggers": [], "reason": "Parked/For-Sale lander on gambling-TLD domain", "challenge_override": False}
            if _is_blocked_page:
                return {"verdict": "blocked", "confidence": 0.95, "category": "access_denied",
                        "key_triggers": [], "reason": "403/Cloudflare block page on gambling-TLD domain", "challenge_override": False}

            operator_cta_signals = detect_operator_cta_signals(cta_buttons)
            if has_providers or has_funnels or operator_cta_signals:
                analyst_verdict = {
                    "verdict": "gambling",
                    "confidence": 0.88,
                    "category": "domain_anchor_gambling",
                    "key_triggers": [domain_signal] + (matched_keywords or []),
                    "reason": f"Gambling domain signal ({domain_signal}) + on-page operator mechanism",
                    "challenge_override": True,
                }
                return await validate_gambling_verdict(url, title, body_text, analyst_verdict, cta_buttons)

        if fast_mode and confidence >= REGULAR_CONVICTION_THRESHOLD and not is_g_domain and not has_gambling_corroboration:
            return {
                "verdict": "regular",
                "confidence": confidence,
                "category": str(round1.get("category", "regular")),
                "key_triggers": round1.get("key_triggers", []),
                "reason": f"fast_mode: Round 1 regular ({confidence:.2f}) accepted without Round 2 challenge",
                "challenge_override": False,
            }

        round2_prompt = f"""URL: {url}
Title: {title}
Page Content Summary: {body_text[:1000]}

Your previous analysis classified this website as "regular" (non-gambling).
You must now cite EXACT VERBATIM text from the page that proves this is NOT a gambling website.

Rules:
1. What is the EXACT core service/product offered on this page?
2. Quote exact phrases from the text that prove it is a legitimate non-gambling service (e.g. banking, education, e-commerce, hospitality, news, government).
3. Legitimate banking institutions (offering savings accounts, fixed deposits, loans, net banking) and government portals are STRICTLY REGULAR.
4. If this page offers poker, card games, sports odds, or casino bonuses, it MUST be classified as gambling.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular",
  "confidence": 0.0 to 1.0,
  "category": "category_name",
  "key_triggers": ["specific elements"],
  "reason": "specific reason",
  "evidence": "exact verbatim text from page"
}}
"""

        r2_messages = [
            {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
            {"role": "user", "content": round2_prompt},
        ]
        round2 = await _call_omniroute(r2_messages, model=OMNIROUTE_MODEL, url=url)

        if round2 is None:
            strong_matches = [k for k in (matched_keywords or []) if k in STRONG_GAMBLING_SIGNALS]
            return {
                "verdict": "unconfirmed",
                "confidence": 0.0,
                "category": "unconfirmed",
                "key_triggers": strong_matches,
                "reason": "challenge_timeout_requeue" + (f" (strong_keywords: {', '.join(strong_matches[:3])})" if strong_matches else ""),
                "challenge_override": False,
            }

        r2_verdict = str(round2.get("verdict", "")).strip().lower()
        r2_confidence = float(round2.get("confidence", 0.5))
        r2_evidence = str(round2.get("evidence", ""))
        r2_reason = str(round2.get("reason", ""))

        if r2_verdict == "gambling":
            r2_analyst = {
                "verdict": "gambling",
                "confidence": r2_confidence,
                "category": str(round2.get("category", "possible_gambling")),
                "key_triggers": round2.get("key_triggers", []),
                "reason": f"Challenge round corrected to gambling: {r2_reason}",
                "challenge_override": True,
            }
            return await validate_gambling_verdict(url, title, body_text, r2_analyst, cta_buttons)

        evidence_convincing, eval_msg = _evaluate_regular_evidence(r2_reason, r2_evidence, body_text, html)

        if not evidence_convincing and has_gambling_corroboration:
            reject_analyst = {
                "verdict": "gambling",
                "confidence": 0.60,
                "category": "possible_gambling",
                "key_triggers": (_operator_ctas or []) + _strong_kw + ([domain_signal] if is_g_domain else []) + (matched_keywords or round2.get("key_triggers", [])),
                "reason": f"Unsubstantiated 'regular' despite gambling signal (Claimed: '{r2_reason[:80]}')",
                "challenge_override": True,
            }
            return await validate_gambling_verdict(url, title, body_text, reject_analyst, cta_buttons)

        if not evidence_convincing:
            return {
                "verdict": "regular",
                "confidence": min(float(r2_confidence), 0.55),
                "category": str(round2.get("category", "regular")),
                "key_triggers": round2.get("key_triggers", []),
                "reason": f"Accepted regular, no gambling signal found ({eval_msg})",
                "challenge_override": False,
            }

        return {
            "verdict": "regular",
            "confidence": r2_confidence,
            "category": str(round2.get("category", "regular")),
            "key_triggers": round2.get("key_triggers", []),
            "reason": f"Ground-truth verified regular: {eval_msg}",
            "challenge_override": False,
        }

    return {
        "verdict": "unconfirmed",
        "confidence": 0.0,
        "category": "unconfirmed",
        "key_triggers": [],
        "reason": "ai_unconfirmed",
        "challenge_override": False,
    }
