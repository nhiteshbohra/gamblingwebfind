from checking_url.ml_classifier import (
    _default_feature_fn,
    predict_proba,
    predict_label,
)


def test_default_feature_fn():
    # Gambling domain
    feat = _default_feature_fn("royalcasino.bet")
    assert "royalcasino" in feat
    assert "tld_bet" in feat
    assert "gamblingtld" in feat

    # Regular benign domain
    feat_reg = _default_feature_fn("wikipedia.org")
    assert "wikipedia" in feat_reg
    assert "tld_org" in feat_reg
    assert "gamblingtld" not in feat_reg


def test_predict_proba_bounds():
    for d in ["royalbet.com", "example.org", "casino777.win"]:
        p = predict_proba(d)
        assert isinstance(p, float)
        assert 0.0 <= p <= 1.0


def test_predict_proba_empty():
    assert predict_proba("") == 0.5


def test_predict_label():
    label = predict_label("testdomain.com")
    assert label in ("gambling", "regular", "uncertain")

