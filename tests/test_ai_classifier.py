from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from checking_url.ai_classifier import (
    parse_ai_json_response,
    detect_gambling_funnels,
    detect_licensed_operator_signals,
    DynamicTimeoutManager,
    start_omniroute_if_needed,
    ensure_omniroute_ready,
    check_omniroute_status,
    AI_TIMEOUT_MIN,
    AI_TIMEOUT_MAX,
)


def test_parse_json_from_response_clean():
    clean_json = '{"verdict": "gambling", "confidence": 0.95, "reason": "Online sports betting exchange"}'
    data = parse_ai_json_response(clean_json)
    assert data is not None
    assert data["verdict"] == "gambling"
    assert data["confidence"] == 0.95


def test_parse_json_from_response_markdown_fenced():
    markdown_response = """
    Here is my analysis of the target site:
    ```json
    {
      "verdict": "regular",
      "confidence": 0.88,
      "reason": "Educational site with academic research"
    }
    ```
    """
    data = parse_ai_json_response(markdown_response)
    assert data is not None
    assert data["verdict"] == "regular"
    assert data["confidence"] == 0.88


def test_parse_json_from_response_invalid():
    invalid_response = "I cannot determine if this is gambling because the page is blank."
    assert parse_ai_json_response(invalid_response) is None


def test_detect_gambling_funnels():
    # Strong cashier/demo funnel
    html_with_funnel = """
    <div>
        <h2>Join India's No 1 Exchange</h2>
        <a href="https://wa.me/919999999999">Get Demo ID on WhatsApp</a>
        <span>Instant Deposit & Instant Withdrawal 24/7</span>
    </div>
    """
    funnels = detect_gambling_funnels(html_with_funnel)
    assert len(funnels) > 0
    assert any("demo id" in f or "wa.me/" in f or "instant deposit" in f for f in funnels)

    # Benign website with whatsapp support but no betting context
    benign_html = """
    <div>
        <h2>Contact Our Customer Support</h2>
        <a href="https://wa.me/919876543210">Chat on WhatsApp</a>
        <p>We sell handmade organic pottery and ceramic dishes.</p>
    </div>
    """
    benign_funnels = detect_gambling_funnels(benign_html)
    assert len(benign_funnels) == 0


def test_detect_licensed_operator_signals():
    text = "This website is licensed and regulated by the Curacao eGaming under license No 1668/JAZ."
    signals = detect_licensed_operator_signals(text)
    assert len(signals) > 0
    assert any("curacao" in s or "licensed" in s for s in signals)

    benign_text = "Welcome to the national gallery of modern art."
    assert len(detect_licensed_operator_signals(benign_text)) == 0


@pytest.mark.asyncio
async def test_dynamic_timeout_manager():
    mgr = DynamicTimeoutManager()
    await mgr.reset_ema()

    # Initial bounds
    assert mgr.compute_timeout(100) >= AI_TIMEOUT_MIN
    assert mgr.compute_timeout(50000) <= AI_TIMEOUT_MAX

    # Recording faster responses lowers EMA
    for _ in range(5):
        await mgr.record_success(5.0)

    t_fast = mgr.compute_timeout(500)
    assert t_fast >= AI_TIMEOUT_MIN

    # Recording timeouts should increase timeout
    await mgr.record_timeout()
    assert mgr.recent_timeout_rate() > 0.0


@pytest.mark.asyncio
async def test_start_omniroute_already_running():
    with patch("checking_url.ai_classifier.check_omniroute_status", new_callable=AsyncMock) as mock_status:
        mock_status.return_value = (True, "OmniRoute Gateway active")
        with patch("subprocess.Popen") as mock_popen:
            res = await start_omniroute_if_needed()
            assert res is True
            mock_popen.assert_not_called()


@pytest.mark.asyncio
async def test_start_omniroute_offline_then_starts():
    status_returns = [
        (False, "Offline"),
        (False, "Offline"),
        (True, "OmniRoute Gateway active"),
    ]
    with patch("checking_url.ai_classifier.check_omniroute_status", side_effect=status_returns):
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc
            res = await start_omniroute_if_needed(timeout_sec=5.0)
            assert res is True
            assert mock_popen.called


@pytest.mark.asyncio
async def test_ensure_omniroute_ready():
    with patch("checking_url.ai_classifier.check_omniroute_status", new_callable=AsyncMock) as mock_status:
        mock_status.return_value = (True, "OmniRoute Gateway active")
        ready = await ensure_omniroute_ready()
        assert ready is True

