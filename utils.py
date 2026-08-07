#!/usr/bin/env python3
"""
utils.py — Shared utility functions for the Gambling Website Detection Tool.
"""

import logging
import os
import random
import re
import time
from urllib.parse import urlparse

import requests

import config


def setup_logging(name: str = "gambling_hunter", level=logging.INFO) -> logging.Logger:
    """Set up a logger that writes to both console and log file."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT))
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT))
    logger.addHandler(fh)

    return logger


def get_random_user_agent() -> str:
    """Return a random User-Agent string."""
    return random.choice(config.USER_AGENTS)


def create_session() -> requests.Session:
    """Create a requests.Session with a random User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": get_random_user_agent()})
    return session


def safe_request(
    url: str,
    method: str = "GET",
    session: requests.Session | None = None,
    timeout: int = config.DEFAULT_TIMEOUT,
    max_retries: int = config.MAX_RETRIES,
    **kwargs,
) -> requests.Response | None:
    """
    Make an HTTP request with retries and error handling.
    Returns the Response object or None on failure.
    """
    logger = logging.getLogger("gambling_hunter")
    s = session or create_session()

    for attempt in range(1, max_retries + 1):
        try:
            resp = s.request(method, url, timeout=timeout, **kwargs)
            return resp
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout ({attempt}/{max_retries}): {url}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error ({attempt}/{max_retries}): {url}")
        except requests.exceptions.TooManyRedirects:
            logger.warning(f"Too many redirects: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error ({attempt}/{max_retries}): {url} — {e}")

        if attempt < max_retries:
            time.sleep(config.RETRY_DELAY)

    return None


def extract_domain(url: str) -> str:
    """Extract the domain name from a URL string."""
    if not url:
        return ""
    # Add scheme if missing so urlparse works
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    # Remove 'www.' prefix for consistency
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.lower().strip()


def sanitize_domain(domain: str) -> str:
    """Clean and normalize a domain string."""
    domain = domain.strip().lower()
    # Remove protocol
    domain = re.sub(r"^https?://", "", domain)
    # Remove path/query
    domain = domain.split("/")[0]
    # Remove port
    domain = domain.split(":")[0]
    # Remove www
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_valid_domain(domain: str) -> bool:
    """Check if a string looks like a valid domain name."""
    if not domain or len(domain) > 253:
        return False
    pattern = re.compile(
        r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.[a-zA-Z0-9-]{1,63})*\.[a-zA-Z]{2,}$"
    )
    return bool(pattern.match(domain))


def is_valid_ip(ip: str) -> bool:
    """Check if a string is a valid IPv4 address."""
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def sanitize_filename(name: str) -> str:
    """Convert a domain name to a safe filename."""
    return re.sub(r"[^\w.\-]", "_", name)


def rate_sleep(delay: float):
    """Sleep for the specified delay with a small random jitter."""
    jitter = random.uniform(0, delay * 0.3)
    time.sleep(delay + jitter)


def read_lines_from_file(filepath: str) -> list[str]:
    """Read non-empty, stripped lines from a text file."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines


def deduplicate(items: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""
    seen = set()
    result = []
    for item in items:
        normalized = item.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
