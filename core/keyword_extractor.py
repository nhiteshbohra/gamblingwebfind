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

