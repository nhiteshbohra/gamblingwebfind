"""
domain_utils.py — URL parsing, normalization, and root domain extraction.
"""

import re
from typing import Optional
import tldextract


def normalize_domain(raw_url_or_domain: str) -> Optional[str]:
    """
    Extracts and returns the normalized root domain from a raw URL or domain string.
    Example:
        'https://sub.betting-site.co.in/path/page.html?ref=1' -> 'betting-site.co.in'
        'http://gambling.com:8080' -> 'gambling.com'
    Returns None if extraction fails or input is invalid.
    """
    if not raw_url_or_domain:
        return None

    cleaned = raw_url_or_domain.strip().lower()

    # If it's an IP address, return cleaned directly
    ip_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
    if re.match(ip_pattern, cleaned):
        return cleaned

    try:
        extracted = tldextract.extract(cleaned)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"
    except Exception:
        pass

    return None


def is_valid_domain(domain: str) -> bool:
    """
    Validates if a string matches basic domain syntax.
    """
    if not domain or len(domain) > 253:
        return False
    
    # Check simple regex for domain structure
    pattern = r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
    return bool(re.match(pattern, domain))
