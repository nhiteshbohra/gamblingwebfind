"""
Regression suite for the false-positive/false-negative incidents this project already hit
(banks marked gambling, real gambling sites marked regular). These are cheap, deterministic
checks against the heuristic layer only (no Ollama/Mongo needed) — run them before trusting
any future change to classifier.py, since every case here was a real production mistake once.

Run with: pytest tests/test_classifier_accuracy.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checking_url.classifier import classify, load_keywords, is_trusted_domain
from checking_url.ai_classifier import detect_licensed_operator_signals, detect_operator_cta_signals

KW = load_keywords()


def test_bank_with_generic_banking_vocabulary_is_regular():
    """The exact incident: a bank page using ordinary banking words ('cashier',
    'instant withdrawal') must not be auto-locked to gambling."""
    html = """<html><title>City Savings Bank</title><body>
    Personal Banking, Business Banking, Fixed Deposit, Net Banking, RTGS, NEFT, IMPS.
    Visit your nearest branch. Ask our cashier for instant withdrawal from your savings account.
    Bonus reward points, win exciting prizes on your credit card. Apply for a home loan today.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://citysavingsbank.com")
    assert decision != "gambling"


def test_institution_with_domain_substring_hit_routes_to_ai_not_instant_lock():
    """A domain containing a loose gambling-keyword substring ('spin' in 'spinal') plus a
    single stray weak word must never instant-lock to gambling without AI review."""
    html = """<html><title>City Spinal Care Clinic</title><body>
    We provide expert spinal treatment and physiotherapy. Book an appointment with our doctors.
    Our patients often win back full mobility. Ask about payment plans and bonus first-visit discount.
    </body></html>"""
    decision, matched = classify(html, KW, url="https://spinalcareclinic.com")
    assert decision != "gambling", f"instant-locked to gambling off domain substring alone: {matched}"


def test_antitrust_site_domain_substring_hit_routes_to_ai_not_instant_lock():
    """'monopoly' is a GAMBLING_DOMAIN_KEYWORD substring but also an ordinary economics term."""
    html = """<html><title>Antitrust Monopoly Watch</title><body>
    We track monopoly and antitrust cases, market concentration reports, and regulatory filings.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://monopolywatch.org")
    assert decision != "gambling"


def test_disguised_betting_site_behind_travel_front_is_not_auto_cleared():
    """Evasion tactic: dressing a real betting site as a travel/hotel page must not be
    dismissed as regular purely on hospitality wording — it needs an AI look."""
    html = """<html><title>Sunrise Travel and Tours</title><body>
    Book your hotel room and enjoy check-in, check-out, free wifi, restaurant dining and spa.
    For our loyal members: place bet now on cricket matches and claim your welcome bonus and jackpot rewards.
    Deposit money instantly and win big!
    </body></html>"""
    decision, _ = classify(html, KW, url="https://sunrisetravelbookings.com")
    assert decision != "regular"


def test_genuine_small_hotel_with_one_stray_word_stays_regular():
    """Counter-test to the above: a real hotel with only trivial noise must not be swept
    into needs_ai by an overcorrected fix."""
    html = """<html><title>Lakeside Resort</title><body>
    Book a room today. Enjoy our restaurant, spa, and free wifi. Our guests always win with our best rates.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://lakesideresort.com")
    assert decision == "regular"


def test_casino_resort_with_hospitality_copy_stays_gambling():
    """A real online casino that also advertises hotel/dining amenities (common for resort
    casino brands) must not be cleared to regular just because of the amenity wording."""
    html = """<html><title>Grand Casino Resort</title><body>
    Welcome to our resort. Enjoy our hotel rooms, fine dining restaurant, spa treatments and free wifi.
    Play online casino games and sports betting anytime, claim welcome bonus now.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://grandcasino.com")
    assert decision == "gambling"


def test_unambiguous_gambling_site_still_locks_gambling():
    """Sanity check: the fixes must not have made real gambling detection weaker."""
    html = """<html><title>Best Online Casino</title><body>
    Play online casino games, sports betting, live casino, slot machines. Deposit now and claim your welcome bonus.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://royalbet888.com")
    assert decision == "gambling"


def test_trusted_allowlist_bypasses_everything():
    """A trusted_domains.json entry must short-circuit to regular even if the page is
    stuffed with gambling vocabulary (defense against a compromised/hijacked page)."""
    assert is_trusted_domain("https://www.hdfcbank.com/some/path")
    decision, matched = classify(
        "<html><title>bet</title>casino win jackpot betting odds</html>",
        KW,
        url="https://www.hdfcbank.com",
    )
    assert decision == "regular"
    assert any(str(m).startswith("allowlist:") for m in matched)


def test_gov_tld_still_regular():
    html = "<html><title>online casino betting</title>jackpot bonus win</html>"
    decision, _ = classify(html, KW, url="https://example.gov.in")
    assert decision == "regular"


def test_gambling_review_site_does_not_instant_lock():
    """The core confusion this project has: a REVIEW/RANKING/AFFILIATE site about gambling
    is saturated with the same strong-signal vocabulary as a real operator, but is not one
    itself. Must never instant-lock to gambling without AI review, on anchored or
    non-anchored domains."""
    html = """<html><title>Best Online Casinos 2026 - Expert Reviews & Rankings</title><body>
    Our team of experts has reviewed and ranked the top online casino and sports betting sites for 2026.
    Compare deposit bonuses, free spins offers, and welcome bonus packages across the best gambling sites.
    Read our in-depth review of each online casino before you sign up. Editor rating: 4.5/5 stars.
    Affiliate disclosure: we may earn commission when you click through to a casino operator.
    Top pick: Casino XYZ - 100% deposit bonus, live casino games, sports betting odds.
    </body></html>"""
    decision_plain, _ = classify(html, KW, url="https://reviewhub2026.com")
    decision_anchored, _ = classify(html, KW, url="https://bestcasinoreviews.com")
    assert decision_plain != "gambling", "non-anchored review site instant-locked to gambling"
    assert decision_anchored != "gambling", "anchored review site instant-locked to gambling"


def test_real_operator_site_still_locks_gambling():
    """Counter-test: the review-site fix must not weaken detection of an actual operator."""
    html = """<html><title>Casino XYZ - Play Now</title><body>
    Welcome to Casino XYZ. Register now and claim your 100% deposit bonus.
    Play live casino games, slots, and sports betting. Login to your account to deposit and withdraw.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://casinoxyz-play.com")
    assert decision == "gambling"


def test_licensed_operator_signal_detects_self_referential_license_language():
    """Live incident: bynton.com, a real UK Gambling Commission-licensed operator, was
    confidently REJECTED by the validator despite its page explicitly stating its license.
    Only self-referential compliance phrasing counts (not bare regulator/charity names)."""
    operator_text = "bynton is operated by bynton and is licensed and regulated in great britain by the gambling commission under account 57245."
    assert detect_licensed_operator_signals(operator_text), "missed a real operator's self-reported license"


def test_licensed_operator_signal_does_not_fire_on_support_charity():
    """Counter-test: a genuine gambling-addiction support charity mentions the same regulator/
    support-body NAMES but never claims to itself be licensed — must not be flagged."""
    charity_text = (
        "gamcare is the leading national charity providing free support for anyone affected by "
        "gambling harms. we work with gamstop and begambleaware to promote responsible gambling."
    )
    assert detect_licensed_operator_signals(charity_text) == [], "flagged a support charity as a licensed operator"


def test_operator_cta_signal_catches_claim_bonus_button():
    """Live incident: juegging-sports.bet's validator claimed 'no login/register-to-play
    mechanism' while the page's own extracted CTA buttons included Login/Withdrawal/CLAIM
    BONUS — a directly falsifiable contradiction this detector exists to catch."""
    cta_buttons = ["Login", "Withdrawal", "Sign Up", "CLAIM BONUS"]
    assert detect_operator_cta_signals(cta_buttons), "missed an unambiguous gambling CTA button"


def test_operator_cta_signal_ignores_generic_account_buttons():
    """A bank or e-commerce site's Login/Withdraw buttons alone must not trigger this —
    only gambling-specific CTA text (claim bonus, bet id, spin, lottery, etc.)."""
    cta_buttons = ["Login", "Withdraw", "Register", "Contact Us"]
    assert detect_operator_cta_signals(cta_buttons) == [], "flagged generic banking-style CTAs as gambling-specific"


def test_gambling_tld_with_zero_content_still_locks_on_liveness_alone():
    """Policy (explicit instruction): for gambling TLDs (.bet/.casino/.poker/.bingo/.lotto),
    content classification is intentionally skipped — any live, non-parked, non-institutional
    page on one of these TLDs locks to "gambling" on liveness alone, even with zero gambling
    vocabulary on the page. (Supersedes the earlier zero-content protection: this trades
    away that precision deliberately, for full coverage on these TLDs.)"""
    html = """<html><title>Sports News and Analysis Blog</title><body>
    Welcome to our blog. We write about sports news, athlete interviews, and league standings.
    Check back weekly for match previews and post-game analysis.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://randomsportsblog.bet")
    assert decision == "gambling"


def test_gambling_tld_with_real_content_still_fast_locks():
    """A real operator on a gambling TLD still locks instantly (no AI round needed)."""
    html = """<html><title>Best Casino</title><body>
    Play online casino games, sports betting, live casino. Deposit now and claim your welcome bonus.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://realcasino.bet")
    assert decision == "gambling"


def test_gambling_tld_parked_lander_still_regular():
    """Counter-test: the liveness-only TLD lock must not swallow the parked/for-sale gate —
    that check runs earlier in classify() and still applies to gambling TLDs."""
    html = """<html><title>domain.bet is for sale</title><body>
    This domain is for sale. Buy this domain today. Contact the owner to make an offer.
    </body></html>"""
    decision, _ = classify(html, KW, url="https://parkedlander.bet")
    assert decision != "gambling"
