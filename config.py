"""
config.py — Central configuration file for Gambling Site Discovery & Intelligence Platform.
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
LOGS_DIR = BASE_DIR / "logs"

# Ensure output directories exist
for folder in [DATA_DIR, SCREENSHOTS_DIR, LOGS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# CSV File Paths
DISCOVERED_CSV = DATA_DIR / "discovered_domains.csv"
CLASSIFIED_CSV = DATA_DIR / "classified_domains.csv"
IP_MAPPING_CSV = DATA_DIR / "ip_mapping.csv"
SITE_INTEL_CSV = DATA_DIR / "site_intelligence.csv"

# SearXNG & Search Settings
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")
SEARXNG_ENGINES = ["google", "bing", "duckduckgo"]
MAX_RESULTS_PER_KEYWORD = 150
MAX_SEARCH_PAGES = 15  # Crawl up to 15 pages of search results per keyword/dork query
KEYWORDS_FILE = BASE_DIR / "keywords.txt"
DORKS_FILE = BASE_DIR / "dorks.txt"

# Daemon & Automation Settings
DAEMON_CYCLE_DELAY = 60  # seconds pause between continuous execution cycles
AUTO_EXPAND_KEYWORDS = True  # Automatically discover new gambling keywords from confirmed sites

# Network & Request Settings
REQUEST_TIMEOUT = 10  # seconds
CONCURRENT_THREADS = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36"
)

HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Reverse IP API Keys (Optional - default uses free endpoints HackerTarget & RapidDNS)
SECURITYTRAILS_API_KEY = os.environ.get("SECURITYTRAILS_API_KEY", "")
SHODAN_API_KEY = os.environ.get("SHODAN_API_KEY", "")

# Playwright Screenshot Settings
SCREENSHOT_VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_TIMEOUT = 25000  # milliseconds
SCREENSHOT_WAIT_UNTIL = "networkidle"

# Classification Keyword Rules & Weights
GAMBLING_POSITIVE_KEYWORDS = [
    "casino", "betting", "satta", "matka", "teen patti", "poker", "roulette",
    "slot", "sportsbook", "wager", "jackpot", "odds", "deposit bonus", "bet",
    "rummy", "andar bahar", "color prediction", "baccarat", "bookmaker",
    "live dealer", "withdrawal", "register now", "claim bonus", "play for cash"
]

NON_GAMBLING_NEGATIVE_KEYWORDS = [
    "gambling addiction", "responsible gambling", "helpline", "wikipedia",
    "news", "article", "review site", "forum", "blog", "government",
    "police", "legal notice", "court", "lawyer", "law firm", "treatment center"
]

# Classification threshold (0 to 1)
GAMBLING_CONFIDENCE_THRESHOLD = 0.4
