"""
checking_url/ai_classifier.py — Local AI (Ollama) Dual-Model Classifier.

Architecture: Analyst + Validator (Judge) Pattern
- Round 1 (gambling-analyst): Standard deep semantic classification
- Round 2 (gambling-analyst): If Round 1 says "regular" → evidence challenge
- Validator (gambling-validator): If analyst says "gambling" → skeptic validator
  challenges the verdict, checks if cited evidence is real, catches false positives

Both models use qwen2.5:3b but with opposing system prompts:
  analyst  → finds gambling signals
  validator → actively looks for reasons the verdict is WRONG
"""
import os
import json
import re
import asyncio
import base64
import logging
from collections import deque
from pathlib import Path
from bs4 import BeautifulSoup
import aiohttp
from dotenv import load_dotenv

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

# ── Configuration ─────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gambling-analyst")
OLLAMA_VALIDATOR_MODEL = os.getenv("OLLAMA_VALIDATOR_MODEL", "gambling-validator")

# ── Ollama Validator Config Guard ───────────────────────────────────────────
# Fixed 2026-08-21 (see ARCHITECTURE.md): production .env once had
# OLLAMA_VALIDATOR_MODEL == OLLAMA_MODEL, and gambling-validator had never
# been built, so every "independent skeptic" Validator round was silently
# just the Analyst model re-confirming its own verdict. That degraded the
# whole two-round challenge system to a single round with no one actually
# checking it, with no error or warning anywhere. Refuse to import this
# module in that state so the same misconfiguration can never again fail
# silently -- set ALLOW_SAME_OLLAMA_VALIDATOR_MODEL=true in .env only for
# deliberate single-model local testing.
if OLLAMA_MODEL == OLLAMA_VALIDATOR_MODEL and os.getenv("ALLOW_SAME_OLLAMA_VALIDATOR_MODEL", "false").lower() != "true":
    raise RuntimeError(
        f"OLLAMA_MODEL and OLLAMA_VALIDATOR_MODEL are both '{OLLAMA_MODEL}' -- the "
        "Validator round would just be the Analyst model validating itself, silently "
        "defeating the two-round challenge system (this is the exact bug fixed on "
        "2026-08-21). Set OLLAMA_VALIDATOR_MODEL to a distinct model in your .env "
        "(e.g. OLLAMA_MODEL=gambling-analyst / OLLAMA_VALIDATOR_MODEL=gambling-validator), "
        "or set ALLOW_SAME_OLLAMA_VALIDATOR_MODEL=true if this is intentional for local testing."
    )
# Tie-breaker: only invoked for the small slice of disputed/uncertain cases (validator says
# "uncertain", or both validator model attempts fail) — a bigger model is affordable there
# since it's a tiny fraction of total volume, unlike running it on every domain. Default is
# 7B (~4.5GB q4) rather than 14B (~9GB) because AI_CONCURRENCY allows overlapping requests
# for DIFFERENT domains at DIFFERENT stages (analyst / validator / tiebreaker), so Ollama can
# end up holding two models resident at once — 7B keeps that safe on a 16GB machine. Bump to
# qwen2.5:14b-instruct-q4_K_M only if you also set OLLAMA_MAX_LOADED_MODELS=1 on the Ollama
# server (forces it to evict the previous model before loading a new one).
OLLAMA_TIEBREAKER_MODEL = os.getenv("OLLAMA_TIEBREAKER_MODEL", "qwen2.5:7b-instruct-q4_K_M")
# Vision fallback for canvas/WebGL-rendered casino UIs (slot reels, live dealer feeds) that
# ship zero DOM text and zero OCR-readable banner text. Kept small on purpose — see
# classify_screenshot_vision() for why "moondream" is the right size for this machine.
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "moondream")
# ponytail: Default AI concurrency set to 2 to give 8B model ample VRAM and zero queue starvation
AI_CONCURRENCY = int(os.getenv("AI_CONCURRENCY", 2))

# Dynamic timeout bounds (seconds) — scaled for 8B validator model
AI_TIMEOUT_MIN = float(os.getenv("AI_TIMEOUT_MIN", 10.0))   # fastest simple pages
AI_TIMEOUT_MAX = float(os.getenv("AI_TIMEOUT_MAX", 60.0))   # slowest complex pages
AI_TIMEOUT_BASE = float(os.getenv("AI_TIMEOUT_BASE", 20.0)) # initial seed before data


class DynamicTimeoutManager:
    """
    Adaptive timeout calculator for Ollama AI requests.

    Logic:
    - Tracks a rolling Exponential Moving Average (EMA) of actual response times.
    - Each request gets a timeout based on:
        (a) Content complexity: longer prompts need more inference time.
        (b) Observed latency: EMA of past response times * safety_multiplier.
    - Bounds: [AI_TIMEOUT_MIN, AI_TIMEOUT_MAX]
    - Quick sites stay fast. Slow/complex sites get the time they need.
    """
    EMA_ALPHA = 0.25        # EMA smoothing factor (higher = adapts faster)
    SAFETY_MULTIPLIER = 1.6 # timeout = ema_time * 1.6 (60% headroom)
    CHARS_PER_SECOND = 150  # approx model chars-per-second throughput baseline

    def __init__(self):
        self._ema_response_time: float = AI_TIMEOUT_BASE  # seed with base
        self._total_calls: int = 0
        self._total_timeouts: int = 0
        self._lock = asyncio.Lock()
        self._recent_outcomes: deque = deque(maxlen=10)  # True = timed out, for recent_timeout_rate()

    def compute_timeout(self, prompt_chars: int) -> float:
        """
        Compute the dynamic timeout for this request.
        Formula:
            content_factor = prompt_chars / CHARS_PER_SECOND
            dynamic_timeout = max(MIN, min(MAX, ema * SAFETY_MULTIPLIER + content_factor))
        """
        content_factor = max(0.0, (prompt_chars - 500) / self.CHARS_PER_SECOND)
        raw = self._ema_response_time * self.SAFETY_MULTIPLIER + content_factor
        clamped = max(AI_TIMEOUT_MIN, min(AI_TIMEOUT_MAX, raw))
        return round(clamped, 1)

    async def record_success(self, elapsed: float):
        """Update EMA with a successful response time."""
        async with self._lock:
            self._total_calls += 1
            self._recent_outcomes.append(False)
            # EMA update: new_ema = alpha * latest + (1 - alpha) * old_ema
            self._ema_response_time = (
                self.EMA_ALPHA * elapsed
                + (1 - self.EMA_ALPHA) * self._ema_response_time
            )

    async def record_timeout(self):
        """Track timeouts — bump EMA conservatively so next request gets a bit more headroom without compounding excessively."""
        async with self._lock:
            self._total_calls += 1
            self._total_timeouts += 1
            self._recent_outcomes.append(True)
            # Conservative 5% bump instead of 20% to prevent runaway 60s timeout freezes
            self._ema_response_time = min(
                AI_TIMEOUT_MAX / self.SAFETY_MULTIPLIER,
                self._ema_response_time * 1.05
            )

    def recent_timeout_rate(self) -> float:
        """Fraction of the last 10 calls that timed out. Used to fail fast (skip waiting
        out another timeout) when Ollama is clearly struggling right now -- NEVER to
        decide a verdict on its own. A high rate only ever means 'ask again later'
        (status stays 'unconfirmed'), same outcome an actual timeout would produce, just
        without burning the wait. Deciding gambling/regular from heuristics alone here
        would bypass the AI review this whole pipeline exists to provide."""
        if not self._recent_outcomes:
            return 0.0
        return sum(self._recent_outcomes) / len(self._recent_outcomes)

    async def reset_ema(self):
        """Reset EMA to base seed — call at start of a new batch to clear inflated timeouts from prior run."""
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


# Global dynamic timeout manager (shared across all calls in a session)
_timeout_mgr = DynamicTimeoutManager()

# Known iGaming game provider CDNs / APIs / iframes
IGAMING_PROVIDERS = (
    # Top Global Live Casino & Slots
    "pragmaticplay", "evolutiongaming", "evolution.com", "spribe", "jiligaming", "ezugi",
    "sexybaccarat", "supernowa", "kingmaker", "habanero", "playngo", "pgsoft",
    "microgaming", "netent", "relax-gaming", "betgames.tv", "fastspin", "cq9gaming",
    "yggdrasil", "redtiger", "bgaming", "playtech", "betsoft", "endorphina",
    "wazdan", "spinomenal", "booming-games", "evoplay", "smartsoft", "nolimitcity",
    "thunderkick", "blueprintgaming", "amatic", "gamomat", "greentube", "novomatic",
    # Asian & Indian targeted networks
    "jili", "sexygaming", "fachai", "dreamgaming", "sa gaming", "wm casino", "allbet"
)

# High-conviction deposit/cashier/VIP funnel hooks — standalone triggers (no gambling context needed)
GAMBLING_FUNNEL_MARKERS_STRONG = (
    "get demo id", "create master id", "whatsapp betting", "telegram betting",
    "instant deposit", "instant withdrawal", "24/7 withdrawal", "fast payout",
    "usdt deposit", "trc20 deposit", "crypto cashier", "betting exchange id"
)

# Social/contact links — only a funnel when gambling context words also appear in the HTML
# wa.me/ and t.me/ appear on millions of legitimate Indian business sites as contact buttons
GAMBLING_FUNNEL_MARKERS_SOCIAL = (
    "wa.me/", "api.whatsapp.com/send", "t.me/", "telegram.me/",
)

# Context words that make a social link a betting funnel
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

    # Word-boundary match required — a bare substring check on short words like "bet" matches
    # inside ordinary English ("between", "alphabet", "diabetes", "Tibetan"), which would let
    # ANY page using the word "between" plus a WhatsApp/Telegram contact link (ubiquitous on
    # legitimate small-business sites) trip this and get instant-locked to gambling downstream.
    has_gambling_context = any(re.search(rf"\b{re.escape(c)}\b", html_lower) for c in _FUNNEL_SOCIAL_CONTEXT)
    if has_gambling_context:
        matched += [m for m in GAMBLING_FUNNEL_MARKERS_SOCIAL if m in html_lower]

    return matched


# Verbatim mentions of known gambling regulators / self-exclusion & problem-gambling support
# bodies are near-zero-noise signals — a non-gambling site essentially never cites these. Small
# local models proved unreliable at weighing this correctly even with explicit prompt
# instructions: live-tested repeatedly against a real UK Gambling Commission-licensed operator
# whose page explicitly said "licensed and regulated... Gambling Commission... GamStop...
# GamCare... BeGambleAware... you can register and place a bet" and was still confidently
# REJECTED as "regular" in some runs, with reasoning that literally acknowledged the licensing
# language and then contradicted itself. This is a code-level safety net: it does NOT auto-lock
# "gambling" (shared hosting/false brand mentions could exist) — it only prevents a validator's
# rejection from being trusted blindly when these signals are present, forcing a tie-breaker
# look instead, in both the "clean" and "hedged" rejection paths.
LICENSED_OPERATOR_SIGNALS = (
    # Deliberately self-referential compliance phrasing only ("we ARE licensed", "our
    # account/license number is...") — NOT bare regulator/charity names like "gamstop" or
    # "gamcare" alone, which legitimately appear on genuine support/awareness/charity/news
    # sites that are emphatically not operators (a real false positive I caught live-testing
    # this exact fix against a gambling-addiction support charity page).
    "licensed and regulated by", "licensed and regulated in", "licensed by the gambling commission",
    "gambling commission account", "under account", "malta gaming authority",
    "curacao egaming", "curacao gaming license", "regulated by the gambling commission",
    "our gambling license", "gaming license number",
)


def detect_licensed_operator_signals(text: str) -> list[str]:
    """Detect verbatim regulator/self-exclusion-body mentions in already-extracted page text."""
    if not text:
        return []
    text_lower = text.lower()
    return [s for s in LICENSED_OPERATOR_SIGNALS if s in text_lower]


# Gambling-specific CTA button text (deliberately excludes generic account-system terms like
# bare "login"/"deposit"/"withdraw" that also appear on banks/e-commerce/forums) — these are
# the subset of clean_page_text()'s cta_patterns that essentially only belong to a gambling
# operator's own interactive elements.
_GAMBLING_SPECIFIC_CTA_MARKERS = (
    "claim bonus", "bet id", "demo id", "bookmaker", "spin", "play now",
    "lottery", "lotto", "scratch", "no-deposit", "free bonus",
)


def detect_operator_cta_signals(cta_buttons: list | None) -> list[str]:
    """Detect gambling-specific interactive CTAs (already extracted from the live page's own
    <a>/<button> tags by clean_page_text()) — proof the SITE ITSELF has operator-style
    interactive elements, not just descriptive text. Used to catch a validator rejection that
    claims "no login/register-to-play mechanism" while the page's own extracted buttons say
    otherwise — this was reproduced live: a validator rejected a real operator (juegging-
    sports.bet) with "does not contain gambling mechanisms such as login/register-to-play"
    while its own cta_buttons list contained "Login", "Withdrawal", and "CLAIM BONUS" — a
    directly falsifiable contradiction the code can catch without relying on the model to
    notice its own inconsistency.
    """
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
    """Detect if HTML *loads assets from* known casino game provider CDNs (script src, iframe src, img src).

    Only matches inside src="..." or data-src="..." attribute values — not free text —
    to avoid false positives where an ad network or analytics tag on a portal page happens
    to contain a provider name as a substring in unrelated text or comment.
    """
    if not html:
        return []
    # Extract all src / data-src values: <... src="VALUE" ...> or <... data-src="VALUE" ...>
    src_values = " ".join(re.findall(r"""(?:data-)?src\s*=\s*["']([^"']{5,300})["']""", html, re.IGNORECASE)).lower()
    return [p for p in IGAMING_PROVIDERS if p in src_values]



# Conviction threshold: if AI says "regular" with confidence below this → send to Round 2 challenge
REGULAR_CONVICTION_THRESHOLD = 0.50

# Non-gambling archetype patterns for ground-truth evidence verification
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
        # Forex / CFD broker specific — these CANNOT appear on gambling sites
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

# Global session and semaphore (tracked with active event loop)
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
    Strips scripts, styling, navbars, tracking, and repetitive whitespace.
    Returns: (title, meta_desc, cta_buttons, trimmed_body_text)
    """
    if not html:
        return "", "", [], ""

    truncated_html = html[:350000]

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

        # Extract interactive CTAs (WhatsApp, Telegram, APK, Deposit, Login, Bookmaker IDs)
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

        # img alt text carries real content on image-heavy pages (banner carousels, promo
        # graphics, jackpot artwork) that get_text() never sees — it only returns text nodes,
        # never attribute values. Collect it before the AI ever sees the page, not just for
        # the heuristic layer's own extractor (see classifier._extract_text).
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
        # Fallback fast regex parser
        clean = re.sub(r'<[^>]+>', ' ', truncated_html)
        trimmed = re.sub(r'\s+', ' ', clean)[:max_chars].strip()
        return "", "", [], trimmed


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

    # Regex extraction of JSON block
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
    """
    Anti-Hallucination Ground-Truth Evaluator:
    1. Checks if the AI's claimed evidence words actually exist in the live page HTML/body_text.
       If the AI claims 'shopping cart' or 'university' but those words are absent from the HTML,
       it is an LLM hallucination -> return (False, 'AI hallucinated non-existent evidence').
    2. Checks if 2+ verified real non-gambling archetype patterns exist in the actual page HTML.
    """
    combined_ai_claim = (reason + " " + evidence).lower()
    page_content = (body_text + " " + (html or "")[:100000]).lower()

    # Check if page has gambling / betting keywords present. Word-boundary match required —
    # a bare substring check on "bet" matches inside ordinary English ("between", "alphabet",
    # "diabetes"), which would wrongly suppress real news/sports-score archetype evidence
    # (below) for any page merely containing the word "between".
    has_gambling_terms = any(re.search(rf"\b{re.escape(kw)}\b", page_content) for kw in (
        "bet", "betting", "casino", "satta", "matka", "poker", "slot", "slots",
        "odds", "bookmaker", "sportsbook", "wagering", "aviator", "roulette"
    ))

    # Verify each claimed archetype against ACTUAL page content
    verified_archetypes = []
    for archetype, patterns in NON_GAMBLING_EVIDENCE_MAP.items():
        # Generic news and sports score markers MUST NOT count as proof of regular for gambling affiliate/sportsbook pages
        if has_gambling_terms and archetype in ("news", "sports_scores_stats"):
            continue

        claimed = any(p in combined_ai_claim for p in patterns)
        if claimed:
            # Verify if the pattern ACTUALLY exists in real page HTML
            actual_matches = [p for p in patterns if p in page_content]
            if actual_matches:
                verified_archetypes.append(f"{archetype}({','.join(actual_matches[:2])})")

    # If 2+ verified distinct non-gambling archetypes exist in real HTML -> Confirmed Regular
    if len(verified_archetypes) >= 2:
        return True, f"Verified real non-gambling evidence in HTML: {', '.join(verified_archetypes)}"

    # If 1 archetype matched with multiple verified terms -> Confirmed Regular
    if len(verified_archetypes) >= 1:
        return True, f"Verified non-gambling evidence: {verified_archetypes[0]}"

    # If AI claimed non-gambling features, but none were verified in HTML -> Hallucination rejected
    return False, "AI claimed non-gambling features that do not exist in page HTML (hallucination rejected)"


async def translate_to_english_if_needed(html: str, url: str = "") -> tuple[str, bool]:
    """Detect a non-English page and translate its visible text to English before
    classification. Both the heuristic keyword scanner (English keyword list) and the
    gambling-analyst prompt (English-only guardrails) are effectively blind to a page in
    Chinese/Russian/Portuguese/etc. -- a real false-negative class for gambling sites
    targeting non-English markets. A cheap offline langdetect check gates this so the
    Ollama round-trip only happens for the rare actually-non-English page.

    Returns (text_for_classification, was_translated). On English content, missing/too-
    short text, or any failure, returns the original html unchanged so callers can use it
    exactly as before.
    """
    if not html:
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

    prompt = (
        "Translate the following webpage text into English. Output ONLY the translated "
        "text with no commentary, no markdown, no notes -- preserve the original meaning "
        f"exactly:\n\n{text[:6000]}"
    )
    payload = {
        "model": "qwen2.5:3b",  # plain base model -- no gambling-analyst persona/system
        # prompt baked in, so it won't bias a translation task.
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }
    sem = get_semaphore()
    async with sem:
        try:
            session = await get_session()
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    translated = (result.get("response") or "").strip()
                    if translated:
                        logger.info(
                            f"[translate] {url} | {lang}->en | {len(text)}->{len(translated)} chars"
                        )
                        return translated, True
        except Exception as e:
            logger.warning(f"[translate] Failed for {url} (lang={lang}): {e}")
    return html, False


async def _call_ollama(prompt: str, url: str = "") -> dict | None:
    """
    Make a single Ollama API call with DYNAMIC per-request timeout.

    Timeout is computed from:
    - Content length (more chars = more inference time needed)
    - EMA of past successful response times (adaptive to current load)
    - Bounded between AI_TIMEOUT_MIN and AI_TIMEOUT_MAX
    """
    import time

    prompt_chars = len(prompt)
    dynamic_timeout = _timeout_mgr.compute_timeout(prompt_chars)

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "top_p": 0.85,
            "num_ctx": 4096,
        },
    }

    sem = get_semaphore()
    async with sem:
        t_start = time.monotonic()
        try:
            session = await get_session()
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=dynamic_timeout),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    elapsed = time.monotonic() - t_start
                    await _timeout_mgr.record_success(elapsed)
                    logger.debug(
                        f"[ai_classifier] {url} | chars={prompt_chars} | "
                        f"timeout_given={dynamic_timeout:.1f}s | "
                        f"actual={elapsed:.1f}s | "
                        f"mgr: {_timeout_mgr.stats()}"
                    )
                    response_text = result.get("response", "")
                    return parse_ai_json_response(response_text)
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError):
            logger.warning(f"[ai_classifier] Ollama server is offline for {url}")
        except asyncio.TimeoutError:
            await _timeout_mgr.record_timeout()
            logger.warning(
                f"[ai_classifier] Timeout after {dynamic_timeout:.1f}s for {url} "
                f"| chars={prompt_chars} | mgr: {_timeout_mgr.stats()}"
            )
        except Exception as e:
            logger.warning(f"[ai_classifier] Inference failed for {url}: {e}")
    return None


async def _call_vision_model(image_path: str, prompt: str, log_ctx: str = "") -> dict | None:
    """Shared Ollama vision-model call: reads the image, sends it + prompt, parses the JSON
    response. Uses a small, purpose-built vision model (default: moondream, ~1.7GB) rather
    than a large general VLM -- a fast targeted image-read, not open-ended reasoning, so a
    lightweight model is the right fit for a 16GB-RAM box also holding Ollama's text model,
    MongoDB, and a Playwright browser pool in memory."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.warning(f"[vision] Could not read screenshot {image_path}: {e}")
        return None

    payload = {
        "model": OLLAMA_VISION_MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": 2048},
    }

    sem = get_semaphore()
    async with sem:
        try:
            session = await get_session()
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=max(30.0, AI_TIMEOUT_MAX)),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return parse_ai_json_response(result.get("response", ""))
                elif resp.status == 404:
                    logger.warning(
                        f"[vision] Model '{OLLAMA_VISION_MODEL}' not found in Ollama — "
                        f"run: ollama pull {OLLAMA_VISION_MODEL}"
                    )
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError):
            logger.warning(f"[vision] Ollama server offline for {log_ctx}")
        except asyncio.TimeoutError:
            logger.warning(f"[vision] Timeout classifying screenshot for {log_ctx}")
        except Exception as e:
            logger.warning(f"[vision] Screenshot classification failed for {log_ctx}: {e}")
    return None


async def classify_screenshot_vision(image_path: str, url: str = "") -> dict | None:
    """
    Vision-model last resort for canvas/WebGL-rendered gambling UIs (slot reels, live-dealer
    video feeds, bet-slip panels, roulette wheels) that render entirely inside a <canvas> or
    WebGL context — these ship ZERO DOM text (the keyword classifier sees nothing) and often
    zero printed banner text either (OCR sees nothing, since there's no static text to read,
    just animated game graphics). Only call this on pages that already look empty everywhere
    else (see runner.py) — it's a targeted patch for one specific blind spot, not a general
    replacement for the text pipeline.
    """
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


async def classify_screenshot_for_sorting(image_path: str, label: str = "") -> dict | None:
    """
    Vision check for checking_url/screenshot_folder_sorter.py: sorting a folder of arbitrary
    screenshots into gambling / not-gambling. Unlike classify_screenshot_vision() (a narrow
    canvas/WebGL fallback for the main pipeline), this is the PRIMARY signal for that tool, so
    it explicitly asks about -- and forces "false" for -- hotels/resorts, restaurants,
    schools/colleges, and banks, even when the image itself shows a casino: a resort's own
    gaming floor or a bank's "jackpot rewards" ad is real gambling-adjacent imagery on a site
    that isn't an online gambling operator, exactly the false-positive class this project has
    hit repeatedly on the text side (see classifier.py's hospitality/banking archetype gates).
    """
    prompt = f"""You are looking at a screenshot of a website{f' ({label})' if label else ''}.

Determine whether this is an ONLINE GAMBLING operator's own site: a casino, sportsbook, slot
game, poker room, or lottery platform where a visitor can register and wager real money on
THIS site.

CRITICAL: If the image shows a hotel/resort, restaurant, school/college/university, or
bank/financial institution, answer "is_gambling": false and "is_institutional": true —
REGARDLESS of any casino, slot machine, or gambling-themed imagery visible (e.g. a resort's
own casino floor, a bank ad using the word "jackpot"). These are real businesses that may
have a physical gaming amenity or gambling-themed marketing, not an online gambling operator,
and must never be counted as gambling here.

Respond ONLY in valid JSON:
{{
  "is_gambling": true or false,
  "is_institutional": true or false,
  "category": "online_casino" | "sports_betting" | "poker" | "lottery" | "hotel_resort" | "school_education" | "bank_financial" | "other_regular",
  "visual_evidence": "what you actually see in the image that supports this"
}}
"""
    return await _call_vision_model(image_path, prompt, log_ctx=label)


async def check_ollama_status() -> tuple[bool, str]:
    """Check if Ollama server is accessible and the model is available."""
    try:
        session = await get_session()
        async with session.get(f"{OLLAMA_BASE_URL}/api/tags") as resp:
            if resp.status == 200:
                data = await resp.json()
                models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                model_base = OLLAMA_MODEL.split(":")[0]
                if model_base in models or any(OLLAMA_MODEL in m.get("name", "") for m in data.get("models", [])):
                    return True, f"Ollama is running with model '{OLLAMA_MODEL}'"
                return True, f"Ollama running, but model '{OLLAMA_MODEL}' not found in: {', '.join(models)}"
            return False, f"Ollama returned HTTP status {resp.status}"
    except Exception as e:
        return False, f"Cannot connect to Ollama at {OLLAMA_BASE_URL}: {e}"


async def start_ollama_if_needed() -> bool:
    """
    Check if Ollama server is running; if not, attempt to start it via subprocess.
    Returns True if Ollama is running/started, False if unavailable.
    """
    global _session
    try:
        import subprocess
        ok, msg = await check_ollama_status()
        if ok:
            print(f"[+] Local AI: {msg}")
            return True

        binary = os.getenv("OLLAMA_AUTOSTART_PATH", "ollama")
        timeout = float(os.getenv("OLLAMA_AUTOSTART_TIMEOUT", "15.0"))
        try:
            subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[!] Ollama: could not start automatically ({e}) — start it manually")
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(1.0)
            ok, msg = await check_ollama_status()
            if ok:
                print(f"[+] Ollama: started ({msg})")
                return True

        print(f"[!] Ollama: could not start automatically — start it manually")
        return False
    finally:
        if _session and not _session.closed:
            await _session.close()
            _session = None


async def stop_ollama_if_running() -> bool:
    """
    Safely release Ollama RAM/VRAM resources when task finishes.
    Sends keep_alive: 0 to Ollama API to unload models from RAM/VRAM immediately.
    """
    global _session
    try:
        session = await get_session()
        for model in (OLLAMA_MODEL, OLLAMA_VALIDATOR_MODEL):
            try:
                async with session.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={"model": model, "keep_alive": 0},
                    timeout=aiohttp.ClientTimeout(total=3.0)
                ):
                    pass
            except Exception:
                pass
        logger.info("[+] Ollama AI models unloaded from RAM/VRAM. Memory released.")
    except Exception as e:
        logger.warning(f"Error unloading Ollama models: {e}")
    finally:
        if _session and not _session.closed:
            await _session.close()
            _session = None
    return True


async def close_ai_session():
    """Close HTTP session and unload Ollama AI models to free system memory."""
    global _session
    await stop_ollama_if_running()
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def classify_with_ai(
    html: str,
    url: str = "",
) -> dict:
    """Legacy compatibility wrapper — calls classify_with_challenge internally."""
    return await classify_with_challenge(html, url)


async def _call_tiebreaker(url: str, title: str, body_text: str, analyst_verdict: dict, dispute_reason: str) -> dict | None:
    """
    Third opinion from a larger model (default qwen2.5:14b), invoked ONLY for the small
    slice of genuinely disputed cases: the validator says "uncertain", or both validator
    model attempts failed. This is deliberately not used on every domain — only a fraction
    of the already-small `needs_ai` bucket ever reaches a dispute, so spending a bigger
    model's extra inference time there is affordable even on a 16GB-RAM machine, whereas
    running it on every domain would not be. Returns None on any failure so the caller can
    fall back to its existing 'unconfirmed' behavior — this never lowers safety, it only
    gives disputed cases a real decisive look instead of an automatic requeue.
    """
    key_triggers = analyst_verdict.get("key_triggers", [])
    # NOTE: unlike the Analyst/Validator rounds, this call has no custom Ollama model with a
    # baked-in SYSTEM prompt behind it -- it hits the raw base model directly via /api/generate
    # with no "system" field in the payload below. That means every guardrail the tiebreaker
    # will ever see has to live in THIS prompt. Because this is also the round consulted for the
    # single hardest slice of cases (the ones two prior models couldn't resolve), it previously
    # carried the thinnest rule set and the smallest page-text window (1500 chars vs 2000-2500
    # for the Analyst/Validator) of the three AI stages -- backwards for the hardest cases.
    # Widened to 2500 chars (matching clean_page_text's own extraction budget) and given the same
    # false-positive guardrails as the Analyst/Validator Modelfiles, condensed for a single-shot
    # prompt (2026-08-24).
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
- A site that REVIEWS, RANKS, or COMPARES gambling operators (affiliate/SEO content: "editor
  rating", "our review", "affiliate disclosure", "we may earn commission", multiple third-party
  brand names being compared, links sending visitors OUT to a different brand's site to
  register/play) is REGULAR — a media/affiliate site, not an operator — no matter how much
  gambling vocabulary appears, AS LONG AS it has no Login/Register-to-play or deposit-to-wager
  system of its OWN for this exact brand.
- The regulator or independent testing lab ITSELF (e.g. eCOGRA, Gaming Laboratories
  International, BMM Testlabs, a government Gambling/Gaming Commission's own official site) is
  REGULAR, not an operator — such pages legitimately discuss "gambling commission" or "gaming
  authority" about THEMSELVES; that is not operator evidence by itself.
- A domain/business name merely containing a gambling-sounding fragment (e.g. "spin" in a
  spinal clinic, "stake" in a stakeholder-relations page, "monopoly" in an antitrust watchdog
  site) is NEVER evidence by itself — judge only the actual page content and purpose.
- If the page states it is "licensed and regulated" by a gambling regulator, cites a gambling
  license/account number, or links to self-exclusion/problem-gambling support bodies (GamStop,
  GamCare, BeGambleAware, or similar) AND it has its own Login/Register-to-play system for this
  exact brand — this is standard, legally required self-description for a real licensed
  gambling operator. Do NOT treat "responsible gambling" language as evidence the site is a
  support/advocacy site rather than an operator; it is the opposite. The bet-slip or game grid
  is often hidden behind login and will not appear in this text — do not require it.
- If the page mixes hospitality/travel/lifestyle wording WITH a genuine betting mechanism (live
  odds, a place-bet button, a betting-ID funnel, a deposit-to-wager flow) on the same page, the
  betting evidence wins — this is a known evasion tactic and must still be called gambling.
- You must pick "gambling" or "regular" — do not say unconfirmed/uncertain.

Respond ONLY in valid JSON:
{{
  "verdict": "gambling" or "regular",
  "confidence": 0.0 to 1.0,
  "reason": "final decisive reason"
}}
"""
    payload = {
        "model": OLLAMA_TIEBREAKER_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "top_p": 0.9, "num_ctx": 4096},
    }
    try:
        session = await get_session()
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=max(60.0, AI_TIMEOUT_MAX * 2)),
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                tb = parse_ai_json_response(result.get("response", ""))
                if tb and str(tb.get("verdict", "")).strip().lower() in ("gambling", "regular"):
                    logger.info(f"[tiebreaker] Resolved disputed verdict for {url}: {tb.get('verdict')} ({dispute_reason})")
                    return {
                        "verdict": str(tb.get("verdict")).strip().lower(),
                        "confidence": float(tb.get("confidence", 0.6)),
                        "category": "tiebreaker_resolved",
                        "key_triggers": key_triggers,
                        "reason": f"Tie-breaker ({OLLAMA_TIEBREAKER_MODEL}) resolved dispute: {tb.get('reason', '')}",
                        "challenge_override": True,
                    }
            elif resp.status == 404:
                logger.warning(f"[tiebreaker] Model '{OLLAMA_TIEBREAKER_MODEL}' not found — run: ollama pull {OLLAMA_TIEBREAKER_MODEL}")
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
    """
    Validator (Judge) Model — challenges a gambling verdict from the analyst.

    Sends the analyst's full output + original page content to gambling-validator,
    which is prompted to act as a skeptic and find reasons the verdict is WRONG.
    If gambling-validator model is missing (HTTP 404), falls back to OLLAMA_MODEL automatically.
    """
    key_triggers = analyst_verdict.get("key_triggers", [])
    analyst_reason = analyst_verdict.get("reason", "")
    analyst_confidence = analyst_verdict.get("confidence", 0.5)
    licensed_operator_signals = detect_licensed_operator_signals(body_text)
    operator_cta_signals = detect_operator_cta_signals(cta_buttons)
    # Combined hard-evidence red flags — any of these existing while the validator rejects
    # "gambling" means the rejection is contradicting directly-checkable page evidence.
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
- If the page states it is "licensed and regulated" by a gambling regulator (e.g. Gambling Commission, Malta Gaming Authority, Curacao), cites a gambling license/account number, links to self-exclusion or problem-gambling support bodies (GamStop, GamCare, BeGambleAware, or similar), or says something like "you can register and place a bet" / "18+ to play" — this is standard, LEGALLY REQUIRED self-description for a real licensed gambling operator, not evidence of a support/advocacy site. Do NOT reject the gambling verdict just because the page talks about "responsible gambling" — every real licensed operator is required to include that exact language. Confirm "gambling" for these unless you find explicit evidence the SITE ITSELF is something else entirely (e.g. it is actually a hotel with rooms to book, or a bank with accounts to open) — the mere presence of compliance/regulatory language on an operator's own page is not that evidence.
- The bet-slip or game grid is very often hidden behind login or loaded by JavaScript and will NOT appear in the page text you were given — do not require it to be visible before confirming gambling.

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

    models_to_try = [OLLAMA_VALIDATOR_MODEL, OLLAMA_MODEL]
    sem = get_semaphore()
    async with sem:
        for model in models_to_try:
            payload = {
                "model": model,
                "prompt": validator_prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.15, "top_p": 0.90, "num_ctx": 4096},
            }
            try:
                session = await get_session()
                async with session.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=max(35.0, _timeout_mgr.compute_timeout(len(validator_prompt)) * 1.5)),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        val_result = parse_ai_json_response(result.get("response", ""))
                        if val_result is None:
                            continue

                        val_verdict = str(val_result.get("verdict", "gambling")).strip().lower()
                        val_validation = str(val_result.get("validation", "confirmed")).strip().lower()
                        rejection_reason = val_result.get("rejection_reason")

                        if val_validation == "rejected" and val_verdict == "regular" and not hard_evidence_signals:
                            # Clean, confident rejection with no licensed-operator red flags —
                            # trust it directly, no need for a tie-breaker.
                            logger.info(
                                f"[validator] REJECTED gambling for {url} ({model}): {rejection_reason}"
                            )
                            return {
                                "verdict": val_verdict,
                                "confidence": val_result.get("confidence", 0.4),
                                "category": "validator_override",
                                "key_triggers": key_triggers,
                                "reason": f"Validator rejected: {rejection_reason or val_result.get('final_reason', '')}",
                                "challenge_override": True,
                            }
                        elif val_validation == "rejected" and val_verdict == "regular" and hard_evidence_signals:
                            # The validator rejected "gambling" despite the page itself citing a
                            # gambling regulator, license number, or self-exclusion/problem-
                            # gambling support body (GamStop/GamCare/BeGambleAware or similar) —
                            # near-zero-noise signals a non-gambling site essentially never
                            # contains. Small local models proved unreliable at weighing this
                            # correctly even with explicit prompt instructions (live-reproduced
                            # against a real licensed operator whose rejection reasoning
                            # acknowledged the licensing language and still said "regular"). Do
                            # not trust a single small model's confident-but-contradicted call —
                            # force a decisive third look instead.
                            dispute_reason = (
                                f"Validator rejected despite licensed-operator signals present in page: "
                                f"{', '.join(hard_evidence_signals[:4])} (validator said: {rejection_reason or val_result.get('final_reason', '')})"
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
                            # Validator disagreed with "gambling" but didn't commit to "regular"
                            # either (e.g. hedged with "unconfirmed") — this is functionally the
                            # same kind of dispute as "uncertain" below (observed live: a resort
                            # page with explicit "online casino games and sports betting" text
                            # got hedged here instead of confirmed) and deserves the same
                            # decisive third look rather than being silently finalized as a hedge.
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
                    f"[validator] Validation with model '{model}' failed for {url}: "
                    f"{type(e).__name__}: {e or 'no error message'}"
                )

    # Every validator model attempt failed (timeout / offline / bad response).
    # Do NOT silently trust the Analyst's unvalidated "gambling" verdict here — that
    # would mean every validator outage becomes a single-model decision with no
    # second opinion, which is exactly where both false positives (Analyst
    # over-triggers on a borderline legit site) and false negatives (Analyst
    # under-triggers on a real gambling site) go unchecked. Route to unconfirmed
    # so it gets requeued instead of shipped straight into a report/ban decision.
    logger.warning(
        f"[validator] All validator models unavailable for {url} — trying tie-breaker "
        f"before falling back to 'unconfirmed' "
        f"(Analyst said '{analyst_verdict.get('verdict')}', "
        f"confidence={analyst_verdict.get('confidence')})"
    )
    tb_result = await _call_tiebreaker(url, title, body_text, analyst_verdict, "validator model(s) unavailable")
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
    """
    Anti-Hallucination 2-Round AI Challenge Classifier with Domain Anchors.

    fast_mode=True: Skip Round 2 challenge — used for unconfirmed re-check to halve AI calls.
    fast_mode=False (default): Full 2-round challenge.
    """
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

    title, meta_desc, cta_buttons, body_text = clean_page_text(html, max_chars=2500)
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
- Official commercial banks, financial institutions (savings, loans, fixed deposits, net banking, Agri/Personal/NRI banking), government websites, educational institutions, or utility calculators are STRICTLY REGULAR. NEVER classify a legitimate bank or financial institution as gambling.
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

    round1 = await _call_ollama(round1_prompt, url)

    # Ollama offline / timeout handler
    # NOTE: Previously this defaulted straight to "gambling" whenever a domain anchor or
    # strong keywords matched — meaning a large share of "gambling" verdicts under load
    # (backlog/timeouts) reflected zero actual AI judgment. Now every timeout/offline case
    # returns "unconfirmed" and gets re-queued (runner.py mode="unconfirmed") instead of
    # being silently counted as a confirmed gambling result. The domain/keyword signal is
    # still surfaced in key_triggers so a human reviewing "unconfirmed" items can prioritize.
    if round1 is None:
        if is_g_domain:
            return {
                "verdict": "unconfirmed",
                "confidence": 0.0,
                "category": "unconfirmed",
                "key_triggers": [domain_signal],
                "reason": f"ollama_timeout_domain_anchor_present({domain_signal})_requeue",
                "challenge_override": False,
            }
        strong_matches = [k for k in (matched_keywords or []) if k in STRONG_GAMBLING_SIGNALS]
        if strong_matches:
            return {
                "verdict": "unconfirmed",
                "confidence": 0.0,
                "category": "unconfirmed",
                "key_triggers": strong_matches,
                "reason": f"ollama_timeout_strong_keywords_present: {', '.join(strong_matches[:3])}_requeue",
                "challenge_override": False,
            }
        return {
            "verdict": "unconfirmed",
            "confidence": 0.0,
            "category": "unconfirmed",
            "key_triggers": [],
            "reason": "ollama_timeout_no_signals",
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
        # Check hospitality gate before considering domain-anchor override
        is_hosp, _ = is_hospitality_site(body_text)
        has_providers = bool(detect_igaming_providers(html))
        has_funnels = bool(detect_gambling_funnels(html))

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

            if not is_hosp or has_providers or has_funnels:
                # Domain anchor confirmed on live page -> route to skeptic validator
                analyst_verdict = {
                    "verdict": "gambling",
                    "confidence": 0.88,
                    "category": "domain_anchor_gambling",
                    "key_triggers": [domain_signal] + (matched_keywords or []),
                    "reason": f"Live gambling domain signal ({domain_signal}) with content: {title[:50]}",
                    "challenge_override": True,
                }
                return await validate_gambling_verdict(url, title, body_text, analyst_verdict, cta_buttons)

        # Low confidence → send to Round 2 evidence challenge instead of an immediate flip
        # fast_mode=True: skip Round 2 ONLY for non-anchored regular domains
        if fast_mode and confidence >= REGULAR_CONVICTION_THRESHOLD and not is_g_domain:
            return {
                "verdict": "regular",
                "confidence": confidence,
                "category": str(round1.get("category", "regular")),
                "key_triggers": round1.get("key_triggers", []),
                "reason": f"fast_mode: Round 1 regular ({confidence:.2f}) accepted without Round 2 challenge",
                "challenge_override": False,
            }

        # High confidence "regular" → Challenge for specific proof
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

        round2 = await _call_ollama(round2_prompt, url)

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

        # Validate Ground Truth in HTML (Anti-Hallucination)
        evidence_convincing, eval_msg = _evaluate_regular_evidence(r2_reason, r2_evidence, body_text, html)

        if not evidence_convincing:
            reject_analyst = {
                "verdict": "gambling",
                "confidence": 0.60,
                "category": "possible_gambling",
                "key_triggers": matched_keywords or round2.get("key_triggers", []),
                "reason": f"{eval_msg} (Claimed: '{r2_reason[:80]}')",
                "challenge_override": True,
            }
            return await validate_gambling_verdict(url, title, body_text, reject_analyst, cta_buttons)

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
        "reason": "ollama_unconfirmed",
        "challenge_override": False,
    }
