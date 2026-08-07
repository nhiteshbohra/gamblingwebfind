#!/usr/bin/env python3
"""
config.py — Central configuration for the Gambling Website Detection Tool.
"""

import os

# ─── Directories ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")

# Create output directories on import
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ─── HTTP Settings ────────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

DEFAULT_TIMEOUT = 15  # seconds
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds between retries

# ─── Rate Limiting ────────────────────────────────────────────────────────────
DORK_DELAY_MIN = 10  # seconds between Google dork queries
DORK_DELAY_MAX = 30
REQUEST_DELAY = 1.0  # seconds between general HTTP requests
REVERSE_IP_DELAY = 1.5  # seconds between reverse IP lookups
LIVENESS_TIMEOUT = 10  # seconds per domain liveness check
WHOIS_DELAY = 2.0  # seconds between WHOIS lookups

# ─── Concurrency ──────────────────────────────────────────────────────────────
LIVENESS_THREADS = 10
INTEL_THREADS = 5
SCREENSHOT_CONCURRENT = 3

# ─── Screenshot Settings ──────────────────────────────────────────────────────
SCREENSHOT_WIDTH = 1920
SCREENSHOT_HEIGHT = 1080
SCREENSHOT_TIMEOUT = 30000  # milliseconds (Playwright uses ms)

# ─── Dorking ──────────────────────────────────────────────────────────────────
MAX_DORK_RESULTS_PER_QUERY = 50

# Domains to exclude from dork results (noise)
DORK_EXCLUDE_DOMAINS = {
    "google.com", "google.co.in", "youtube.com", "wikipedia.org",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "reddit.com", "quora.com", "medium.com",
    "amazon.com", "flipkart.com", "github.com", "stackoverflow.com",
    "apple.com", "microsoft.com", "bbc.com", "cnn.com",
    "timesofindia.indiatimes.com", "ndtv.com", "thehindu.com",
    "news18.com", "hindustantimes.com", "indianexpress.com",
    "play.google.com", "apps.apple.com",
}

# ─── Reverse IP Sources ──────────────────────────────────────────────────────
REVERSE_IP_SOURCES = ["hackertarget", "rapiddns"]

# ─── Geo IP ───────────────────────────────────────────────────────────────────
GEO_IP_API = "http://ip-api.com/json/{ip}?fields=status,country,city,isp,org,as"
GEO_IP_RATE_LIMIT = 45  # max requests per minute (free tier)

# ─── CSV Column Order ────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "domain",
    "ip",
    "reverse_ip_domain_count",
    "reverse_ip_domains",
    "status",
    "http_code",
    "response_time_ms",
    "final_url",
    "page_title",
    "meta_description",
    "meta_keywords",
    "content_language",
    "server",
    "technologies",
    "ssl_issuer",
    "ssl_expiry",
    "whois_registrar",
    "whois_created",
    "whois_expires",
    "whois_country",
    "ip_country",
    "ip_city",
    "ip_isp",
    "screenshot_path",
    "discovery_source",
    "scan_timestamp",
]

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(OUTPUT_DIR, "scan_log.txt")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
