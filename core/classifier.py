import urllib.parse
from bs4 import BeautifulSoup

EXCLUDED_DOMAINS = {
    'google.com', 'apple.com', 'microsoft.com', 'yahoo.com', 'bing.com', 'baidu.com',
    'youtube.com', 'facebook.com', 'twitter.com', 'x.com', 'instagram.com', 'tiktok.com',
    'reddit.com', 'linkedin.com', 'pinterest.com', 'telegram.org', 't.me', 'medium.com',
    'wikipedia.org', 'wikihow.com', 'quora.com', 'vimeo.com', 'break.com',
    'al.com', 'bloomberg.com', 'forbes.com', 'reuters.com', 'bbc.com', 'cnn.com', 'theguardian.com',
    'syracuse.com', 'nj.com', 'cleveland.com', 'pennlive.com', 'oregonlive.com', 'masslive.com',
    'github.com', 'gitlab.com', 'stackoverflow.com', 'wordpress.org', 'play.google.com',
    'statista.com', 'casinosadvisor.org', 'revpanda.com', 'bragg.group', 'trustpilot.com',
    'dimers.com', 'actionnetwork.com', 'oddschecker.com', 'pokernews.com', 'sportsbettingdime.com',
    'betting.net', 'oddsportal.com', 'covers.com', 'deadspin.com', 'hellomonaco.com',
    'brulosophy.com', 'indiabythenile.com', 'emba.com.bo', 'rapreviews.com', 'coincodex.com',
    'sailgp.com', 'crossingbroad.com', 'gametyrant.com', 'translationroyale.com',
    'oddsmonkey.com', 'playtoday.co', 'somuchpoker.com', 'prepscholar.com', 'topendsports.com'
}

STRONG_SIGNALS = [
    "deposit bonus", "free spins", "place a bet", "live dealer",
    "withdraw winnings", "register now", "sign up now", "claim bonus",
    "play now", "cashier", "bet slip", "mga/", "ukgc", "curaçao egaming", "curacao egaming"
]

MEDIUM_SIGNALS = [
    "casino", "slots", "poker", "sportsbook", "jackpot", "wager", "betting"
]

STRUCTURAL_SIGNALS = [
    "18+", "gambleaware", "responsible gambling", "be-gamble-aware", "age verification"
]

NEGATIVE_SIGNALS = [
    "how to play", "wikihow", "news article", "editorial policy", "written by", "author bio",
    "in-app purchases", "app store", "google play", "adult content", "porn"
]


def _extract_text(html: str) -> str:
    """Return visible text + title + meta description content, all lowercased."""
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


def classify(html: str, domain: str = "", url: str = "", threshold=0.55):
    if not html:
        return False, 0.0, ["empty_html"]

    # Exclude blacklisted non-operator domains immediately
    check_targets = [d.lower().strip() for d in (domain, url) if d]
    for target in check_targets:
        for excluded in EXCLUDED_DOMAINS:
            if excluded in target:
                return False, 0.0, [f"excluded_domain:{excluded}"]

    text = _extract_text(html)
    matched_reasons = []
    score = 0.0

    for sig in STRONG_SIGNALS:
        if sig in text:
            score += 0.25
            matched_reasons.append(f"strong:{sig}")

    for sig in MEDIUM_SIGNALS:
        if sig in text:
            score += 0.10
            matched_reasons.append(f"medium:{sig}")

    for sig in STRUCTURAL_SIGNALS:
        if sig in text:
            score += 0.15
            matched_reasons.append(f"structural:{sig}")

    for sig in NEGATIVE_SIGNALS:
        if sig in text:
            score -= 0.15
            matched_reasons.append(f"negative:{sig}")

    confidence_score = max(0.0, min(round(score, 2), 1.0))
    is_gambling = confidence_score >= threshold
    return is_gambling, confidence_score, matched_reasons
