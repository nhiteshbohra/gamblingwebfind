"""
checking_url/deep_crawl.py — Extract outbound gambling links from a classified gambling page.

When a page is classified as a gambling AGGREGATOR (a site that lists many gambling URLs),
this module scrapes all outbound external links from the page HTML, filters them to only
keep likely gambling-related domains, and returns them for seeding into domain_Listed.

Logic:
  1. Parse all <a href="..."> links from the fetched HTML using BeautifulSoup
  2. Extract the root domain from each link using tldextract
  3. Skip: same domain, social media, search engines, ad/tracker networks, etc.
  4. Skip: .gov / .edu TLDs
  5. Keep: links containing known gambling URL keywords OR any external domain
     (depending on DEEP_CRAWL_STRICT setting)
  6. Return set of clean domain strings ready to upsert into domain_Listed
"""

import os
import re
from urllib.parse import urljoin, urlparse

import tldextract
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

# Minimum number of outbound links found to consider a page an "aggregator"
# and trigger deep crawl saving. Set to 1 to always save.
MIN_OUTBOUND_LINKS = int(os.getenv("DEEP_CRAWL_MIN_LINKS", 3))

# If True: only save links whose domain/URL contains a gambling keyword.
# If False: save ALL external domains found on gambling pages (more greedy).
STRICT_MODE = os.getenv("DEEP_CRAWL_STRICT", "false").strip().lower() == "true"

# ── Blocklists ────────────────────────────────────────────────────────────────

_BLOCKED_DOMAINS = {
    # Social media
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "linkedin.com", "tiktok.com", "pinterest.com", "reddit.com", "snapchat.com",
    "telegram.org", "t.me", "discord.com", "discord.gg", "whatsapp.com",
    # Search / Big tech
    "google.com", "google.co.in", "bing.com", "yahoo.com", "duckduckgo.com",
    "apple.com", "microsoft.com", "amazon.com", "cloudflare.com",
    # News / reference
    "wikipedia.org", "bbc.com", "cnn.com", "reuters.com", "forbes.com",
    "nytimes.com", "theguardian.com",
    # Ad / tracking / analytics
    "doubleclick.net", "googlesyndication.com", "googletagmanager.com",
    "googleadservices.com", "analytics.google.com", "hotjar.com",
    "adroll.com", "criteo.com", "outbrain.com", "taboola.com",
    # Domain registrars / parking
    "godaddy.com", "namecheap.com", "sedo.com", "dan.com", "afternic.com",
    "hugedomains.com", "parkingcrew.net",
    # Payment / tools
    "paypal.com", "stripe.com", "visa.com", "mastercard.com",
    "github.com", "stackoverflow.com", "wordpress.org", "wp.com",
}

_BLOCKED_TLDS = {"gov", "edu", "mil"}

# Gambling-related URL keywords (for STRICT_MODE filtering)
_GAMBLING_URL_KEYWORDS = [
    "casino", "bet", "poker", "slot", "bingo", "lottery", "gambl",
    "wager", "spin", "jackpot", "roulette", "blackjack", "sportsbook",
    "odds", "punt", "prize", "win", "lotto", "sweepstake", "chips",
    "cards", "dice", "craps", "keno", "bonus",
]


def _is_blocked(domain: str, ext) -> bool:
    """Return True if domain should be skipped."""
    if not domain:
        return True
    if ext.suffix in _BLOCKED_TLDS:
        return True
    # Check against blocklist (exact match on registered domain)
    reg = ext.registered_domain.lower() if ext.registered_domain else domain.lower()
    if reg in _BLOCKED_DOMAINS:
        return True
    # Check subdomain + registered together
    if domain.lower() in _BLOCKED_DOMAINS:
        return True
    return False


def _looks_like_gambling_url(domain: str) -> bool:
    """Return True if the domain string contains a gambling keyword."""
    d = domain.lower()
    return any(kw in d for kw in _GAMBLING_URL_KEYWORDS)


def extract_outbound_domains(html: str, source_url: str) -> set[str]:
    """
    Parse HTML and return a set of clean outbound domain strings that are
    likely gambling-related external sites not matching the source domain.

    Args:
        html:       Full HTML string of the fetched page
        source_url: The URL of the page being parsed (to filter out self-links)

    Returns:
        Set of domain strings e.g. {"bet365.com", "pokerstars.com", ...}
    """
    if not html:
        return set()

    source_ext = tldextract.extract(source_url)
    source_reg = source_ext.registered_domain.lower()

    soup = BeautifulSoup(html, "html.parser")
    found_domains: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue

        # Resolve relative URLs to absolute
        try:
            abs_url = urljoin(source_url, href)
        except Exception:
            continue

        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue

        ext = tldextract.extract(abs_url)
        if not ext.registered_domain:
            continue

        reg = ext.registered_domain.lower()

        # Skip self-links
        if reg == source_reg:
            continue

        # Skip blocked domains / TLDs
        if _is_blocked(reg, ext):
            continue

        # In strict mode: only keep if domain looks like gambling
        if STRICT_MODE and not _looks_like_gambling_url(reg):
            continue

        found_domains.add(reg)

    return found_domains
