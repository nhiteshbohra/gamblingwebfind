import os
import re
from collections import Counter
from bs4 import BeautifulSoup

def load_stopwords(stopwords_file="config/stopwords.txt"):
    if os.path.exists(stopwords_file):
        with open(stopwords_file, 'r', encoding='utf-8') as f:
            return set(line.strip().lower() for line in f if line.strip())
    return set()

def extract_keywords(html: str, max_keywords=5, stopwords_file="config/stopwords.txt", existing_terms: set = None):
    """Extract candidate keyword phrases from page HTML.
    existing_terms: set of terms already in the keywords table — pre-filtered out before returning.
    """
    if not html:
        return []

    stopwords = load_stopwords(stopwords_file)
    existing = existing_terms or set()

    soup = BeautifulSoup(html, 'html.parser')
    for script in soup(["script", "style"]):
        script.decompose()

    text = soup.get_text(separator=' ').lower()
    words = re.findall(r'\b[a-z]{3,15}\b', text)

    # Filter stopwords
    filtered_words = [w for w in words if w not in stopwords]

    # Extract 2-gram phrases & 1-gram words
    bigrams = [f"{filtered_words[i]} {filtered_words[i+1]}" for i in range(len(filtered_words)-1)]

    candidates = filtered_words + bigrams
    counts = Counter(candidates)

    extracted = []
    for term, _ in counts.most_common(max_keywords * 3):
        if term not in stopwords and term not in existing and len(term) > 3:
            extracted.append(term)
            if len(extracted) >= max_keywords:
                break

    return extracted


def extract_dorks(html: str, domain: str = "", stopwords_file="config/stopwords.txt") -> list:
    """Extract candidate dork queries from page HTML and domain.
    Generates intitle:, inurl: search dorks.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup(["script", "style"]):
        script.decompose()

    text = soup.get_text(separator=' ').lower()
    words = re.findall(r'\b[a-z]{3,15}\b', text)
    stopwords = load_stopwords(stopwords_file)

    filtered = [w for w in words if w not in stopwords]
    bigrams = [f"{filtered[i]} {filtered[i+1]}" for i in range(len(filtered) - 1)]

    counts = Counter(bigrams)
    top_phrases = [phrase for phrase, _ in counts.most_common(3)]

    dorks = []
    for phrase in top_phrases:
        dorks.append(f'intitle:"{phrase}"')
        dorks.append(f'"{phrase}" "login"')
        dorks.append(f'"{phrase}" "bonus"')

    if domain:
        clean_name = domain.replace('.com', '').replace('.org', '').replace('.net', '').replace('.in', '').split('.')[0]
        if len(clean_name) >= 3:
            dorks.append(f'inurl:"{clean_name}"')

    return list(dict.fromkeys(dorks))

