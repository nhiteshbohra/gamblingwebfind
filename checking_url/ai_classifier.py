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
import logging
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
            # EMA update: new_ema = alpha * latest + (1 - alpha) * old_ema
            self._ema_response_time = (
                self.EMA_ALPHA * elapsed
                + (1 - self.EMA_ALPHA) * self._ema_response_time
            )

    async def record_timeout(self):
        """Track timeouts — if too many, bump EMA slightly to get more headroom."""
        async with self._lock:
            self._total_calls += 1
            self._total_timeouts += 1
            # Bump EMA upward so next request gets a bit more time
            self._ema_response_time = min(
                AI_TIMEOUT_MAX / self.SAFETY_MULTIPLIER,
                self._ema_response_time * 1.2
            )

    async def reset_ema(self):
        """Reset EMA to base seed — call at start of a new batch to clear inflated timeouts from prior run."""
        async with self._lock:
            self._ema_response_time = AI_TIMEOUT_BASE
            self._total_calls = 0
            self._total_timeouts = 0

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

    has_gambling_context = any(c in html_lower for c in _FUNNEL_SOCIAL_CONTEXT)
    if has_gambling_context:
        matched += [m for m in GAMBLING_FUNNEL_MARKERS_SOCIAL if m in html_lower]

    return matched


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

        for tag in soup(["script", "style", "svg", "noscript", "iframe", "path"]):
            tag.decompose()

        body_text = soup.get_text(separator=" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)

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


    async def record_timeout(self):
        """Track timeouts — bump EMA conservatively so next request gets a bit more headroom without compounding excessively."""
        async with self._lock:
            self._total_calls += 1
            self._total_timeouts += 1
            # Conservative 5% bump instead of 20% to prevent runaway 60s timeout freezes
            self._ema_response_time = min(
                AI_TIMEOUT_MAX / self.SAFETY_MULTIPLIER,
                self._ema_response_time * 1.05
            )


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

    # Check if page has gambling / betting keywords present
    has_gambling_terms = any(kw in page_content for kw in (
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


async def validate_gambling_verdict(
    url: str,
    title: str,
    body_text: str,
    analyst_verdict: dict,
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

    validator_prompt = f"""URL: {url}
Title: {title}
Page Content: {body_text[:1200]}

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

                        val_verdict = val_result.get("verdict", "gambling")
                        val_validation = val_result.get("validation", "confirmed")
                        rejection_reason = val_result.get("rejection_reason")

                        if val_validation == "rejected" and val_verdict != "gambling":
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
                        elif val_validation == "uncertain":
                            return {
                                "verdict": "unconfirmed",
                                "confidence": 0.4,
                                "category": "validator_uncertain",
                                "key_triggers": key_triggers,
                                "reason": f"Validator uncertain: {val_result.get('final_reason', '')}",
                                "challenge_override": False,
                            }
                        else:
                            confirmed_result = dict(analyst_verdict)
                            confirmed_result["reason"] = (
                                f"{analyst_reason} [Validated: {val_result.get('evidence_check', '')[:100]}]"
                            )
                            return confirmed_result
            except Exception as e:
                logger.warning(f"[validator] Validation with model '{model}' failed for {url}: {e}")

    return analyst_verdict


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
        return await validate_gambling_verdict(url, title, body_text, analyst_verdict)

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
                return await validate_gambling_verdict(url, title, body_text, analyst_verdict)

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
            return await validate_gambling_verdict(url, title, body_text, r2_analyst)

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
            return await validate_gambling_verdict(url, title, body_text, reject_analyst)

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
