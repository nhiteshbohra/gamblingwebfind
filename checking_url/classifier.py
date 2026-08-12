"""
checking_url/classifier.py — Direct keyword matching classifier.

Loads keywords directly from gambling_top_500_keywords.json.
Rule: If >= 3 keywords are found in page HTML, site is marked as gambling.
"""
import json
from pathlib import Path
from bs4 import BeautifulSoup

# Path to the 500 keywords JSON file
KEYWORDS_FILE = Path(__file__).resolve().parent.parent / "gambling_top_500_keywords.json"


def load_keywords() -> set:
    """Load keywords from gambling_top_500_keywords.json into a set."""
    if KEYWORDS_FILE.exists():
        with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
            terms = json.load(f)
            kw_set = set(str(kw).strip().lower() for kw in terms if kw)
            print(f"[classifier] Loaded {len(kw_set)} keywords from {KEYWORDS_FILE.name}")
            return kw_set
    print(f"[classifier WARNING] {KEYWORDS_FILE.name} not found!")
    return set()


def _extract_text(html: str) -> str:
    """Return visible text + title + meta description, lowercased."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(["script", "style"]):
        tag.decompose()
    parts = [soup.get_text(separator=' ')]
    title = soup.find('title')
    if title and title.string:
        parts.append(title.string)
    for meta in soup.find_all('meta', attrs={'name': True, 'content': True}):
        if meta['name'].lower() in ('description', 'keywords'):
            parts.append(meta['content'])
    return ' '.join(parts).lower()


def classify(html: str, url: str = "", keywords: set = None, min_keywords: int = 3) -> tuple[bool, list[str]]:
    """Classifier:
    - Hard excludes .gov / .edu domains.
    - Counts matching keywords from the 500 keyword set.
    - Returns (True, matched_keywords) if len(matched_keywords) >= min_keywords (default 3).
    - Returns (False, matched_keywords) otherwise.
    """
    if not html:
        return False, ["empty_html"]

    url_lower = url.lower()
    if '.gov' in url_lower or '.edu' in url_lower:
        return False, ["hard_excluded:gov_edu"]

    text = _extract_text(html)
    kw_set = keywords if keywords is not None else load_keywords()

    # Find all matching keywords present in page text
    matched = [kw for kw in kw_set if kw in text]

    is_gambling = len(matched) >= min_keywords
    return is_gambling, matched
