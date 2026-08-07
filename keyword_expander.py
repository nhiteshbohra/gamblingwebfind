"""
keyword_expander.py — Dynamic Keyword & Dork Auto-Expansion Engine.
Analyzes confirmed gambling sites to auto-discover new search terms and dorks.
"""

import csv
import logging
import re
from typing import List, Set
from collections import Counter

import config

logging.basicConfig(
    filename=config.LOGS_DIR / "keyword_expander.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Common words to filter out when extracting terms
STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "your", "are", "have",
    "all", "best", "top", "free", "get", "new", "play", "site", "online", "http",
    "https", "com", "net", "org", "www", "home", "page", "welcome", "official"
}


def load_existing_keywords() -> Set[str]:
    keywords = set()
    if config.KEYWORDS_FILE.exists():
        with open(config.KEYWORDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip().lower()
                if cleaned and not cleaned.startswith("#"):
                    keywords.add(cleaned)
    return keywords


def load_existing_dorks() -> Set[str]:
    dorks = set()
    if config.DORKS_FILE.exists():
        with open(config.DORKS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip().lower()
                if cleaned and not cleaned.startswith("#"):
                    dorks.add(cleaned)
    return dorks


def extract_candidate_terms() -> Set[str]:
    """
    Extracts recurring N-grams (2-3 words) from site titles and meta descriptions of confirmed sites.
    """
    candidate_phrases = []

    if config.CLASSIFIED_CSV.exists():
        with open(config.CLASSIFIED_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("is_gambling") == "True" and row.get("title"):
                    title = row["title"].lower()
                    words = re.findall(r"\b[a-z]{3,}\b", title)
                    # Extract bi-grams and tri-grams
                    for i in range(len(words) - 1):
                        if words[i] not in STOP_WORDS and words[i+1] not in STOP_WORDS:
                            candidate_phrases.append(f"{words[i]} {words[i+1]}")
                    for i in range(len(words) - 2):
                        if words[i] not in STOP_WORDS and words[i+2] not in STOP_WORDS:
                            candidate_phrases.append(f"{words[i]} {words[i+1]} {words[i+2]}")

    counts = Counter(candidate_phrases)
    # Pick phrases that appear at least twice
    frequent_terms = {phrase for phrase, count in counts.items() if count >= 2}
    return frequent_terms


def auto_expand_keywords() -> List[str]:
    """
    Appends new candidate terms to keywords.txt and generates new dorks into dorks.txt.
    """
    existing_keywords = load_existing_keywords()
    existing_dorks = load_existing_dorks()
    new_terms = extract_candidate_terms()

    added_keywords = []
    added_dorks = []

    for term in new_terms:
        if term not in existing_keywords:
            added_keywords.append(term)
            existing_keywords.add(term)

        # Generate dork variations
        dork_1 = f'intitle:"{term}"'
        dork_2 = f'inurl:"{term.replace(" ", "")}"'
        
        if dork_1 not in existing_dorks:
            added_dorks.append(dork_1)
            existing_dorks.add(dork_1)
        if dork_2 not in existing_dorks:
            added_dorks.append(dork_2)
            existing_dorks.add(dork_2)

    # Append to keywords.txt
    if added_keywords:
        with open(config.KEYWORDS_FILE, "a", encoding="utf-8") as f:
            for kw in added_keywords:
                f.write(f"{kw}\n")
        print(f"[+] Keyword Expander: Added {len(added_keywords)} new seed keywords to {config.KEYWORDS_FILE.name}")
        logging.info(f"Added {len(added_keywords)} keywords to {config.KEYWORDS_FILE}")

    # Append to dorks.txt
    if added_dorks:
        with open(config.DORKS_FILE, "a", encoding="utf-8") as f:
            for dork in added_dorks:
                f.write(f"{dork}\n")
        print(f"[+] Keyword Expander: Added {len(added_dorks)} new dork queries to {config.DORKS_FILE.name}")
        logging.info(f"Added {len(added_dorks)} dorks to {config.DORKS_FILE}")

    return added_keywords


if __name__ == "__main__":
    auto_expand_keywords()
