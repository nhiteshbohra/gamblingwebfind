from checking_url.classifier import (
    classify,
    is_dead_or_error_page,
    detect_negative_archetype,
    is_gambling_domain,
    is_parked_or_for_sale,
)


def test_is_dead_or_error_page():
    # Standard dead/error strings
    is_dead, reason = is_dead_or_error_page("<html><body>502 Bad Gateway</body></html>")
    assert is_dead is True
    assert "502 bad gateway" in reason.lower()

    is_dead, reason = is_dead_or_error_page("Welcome to nginx! Web server is successfully installed.")
    assert is_dead is True

    # Regular live site
    is_dead, _ = is_dead_or_error_page("Welcome to our online store. Browse our latest fashion collection.")
    assert is_dead is False


def test_detect_negative_archetype():
    # Calculator / utility archetype
    calc_text = "Free online financial tools: GST calculator, EMI calculator, and loan calculator to calculate monthly interest and payments."
    is_neg, reason = detect_negative_archetype(calc_text)
    assert is_neg is True
    assert "calculator" in reason

    # Plain gambling site should NOT trigger negative archetype
    casino_text = "Play online roulette and slots. Live dealer baccarat, deposit now to win real money."
    is_neg, _ = detect_negative_archetype(casino_text)
    assert is_neg is False


def test_is_gambling_domain():
    # TLD based
    assert is_gambling_domain("luckyvegas.casino")[0] is True
    assert is_gambling_domain("cricketbet.bet")[0] is True

    # Keyword based
    assert is_gambling_domain("bet365bonus.com")[0] is True
    assert is_gambling_domain("purecasinoreview.com")[0] is True

    # Benign non-gambling domain
    assert is_gambling_domain("microsoft.com")[0] is False
    assert is_gambling_domain("wikipedia.org")[0] is False


def test_is_parked_or_for_sale():
    parked_text = "This domain is for sale. Inquire today at GoDaddy or Sedo to buy this domain."
    is_parked, markers = is_parked_or_for_sale(parked_text, url="randomname12345.com")
    assert is_parked is True
    assert len(markers) > 0

    active_site = "Explore our cloud computing infrastructure, security, and developer tools."
    is_parked, markers = is_parked_or_for_sale(active_site, url="mytechcompany.io")
    assert is_parked is False


def test_classify_benign_site():
    html = "<html><body><h1>Tech Blog</h1><p>Learn Python programming, async await syntax, and machine learning tutorials.</p></body></html>"
    status, matched = classify(html, url="pythontechblog.com")
    assert status == "regular"


def test_classify_unambiguous_gambling():
    html = """
    <html><body>
    <h1>Welcome to RoyalBet Casino</h1>
    <p>Play live dealer roulette, online slots, and teen patti.</p>
    <p>Sign up bonus: 100% first deposit bonus. Bet now and deposit money via UPI for instant withdrawal.</p>
    </body></html>
    """
    status, matched = classify(html, url="royalbet.casino")
    assert status in ("gambling", "needs_ai")
    assert len(matched) > 0

