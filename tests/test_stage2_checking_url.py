"""
tests/test_stage2_checking_url.py — Unit and integration tests for Stage 2 checking, classification, and runner.
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from checking_url.classifier import classify, is_hospitality_site
from checking_url.fetcher import _classify_failure, fetch, FetchResult
from checking_url.ai_classifier import DynamicTimeoutManager, classify_with_challenge
from checking_url.runner import run as check_run
from db.mongo_client import source_domains, checked_domains, write_result


class TestStage2Classifier:
    """Test heuristic keyword threshold classification."""

    def test_classify_high_density_gambling(self, sample_html_gambling, sample_keywords):
        decision, matched = classify(sample_html_gambling, keywords=set(sample_keywords))
        assert decision == "gambling"
        assert len(matched) >= 5

    def test_classify_low_density_regular(self, sample_html_regular, sample_keywords):
        decision, matched = classify(sample_html_regular, keywords=set(sample_keywords))
        assert decision == "regular"
        assert len(matched) < 3

    def test_classify_moderate_density_needs_ai(self, sample_keywords):
        # 3 keywords
        html = "<html><body>We offer roulette and blackjack with occasional free spins in games.</body></html>"
        decision, matched = classify(html, keywords=set(sample_keywords))
        assert decision == "needs_ai"
        assert len(matched) in (3, 4)

    def test_classify_empty_html(self, sample_keywords):
        decision, matched = classify("", keywords=set(sample_keywords))
        assert decision == "regular"
        assert matched == []


class TestStage2Fetcher:
    """Test HTTP failure classification and fetcher behavior."""

    def test_classify_failure_status_codes(self):
        assert _classify_failure(status_code=403) == "blocked"
        assert _classify_failure(status_code=429) == "blocked"
        assert _classify_failure(status_code=404) == "dead_confirmed"
        assert _classify_failure(error="Connection refused") == "connection_failed"

    def test_classify_failure_body_sniffing(self):
        # Cloudflare block marker
        cf_html = "<html><head><title>Just a moment...</title></head><body>cf-challenge ray id 123</body></html>"
        assert _classify_failure(html=cf_html) == "blocked"

        # Domain for sale / parking marker
        parked_html = "<html><body>This domain is for sale. Buy this domain at GoDaddy.com</body></html>"
        assert _classify_failure(html=parked_html) == "dead_confirmed"

    @pytest.mark.asyncio
    async def test_fetch_success_mock(self, sample_html_gambling):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.body = sample_html_gambling.encode("utf-8")
        mock_resp.encoding = "utf-8"

        with patch("scrapling.fetchers.AsyncFetcher.get", AsyncMock(return_value=mock_resp)):
            result = await fetch("https://testcasino.com", "testcasino.com", timeout_seconds=1, per_domain_delay=0)
            assert result.status_code == 200
            assert result.failure_type is None
            assert "Welcome to Royal" in result.html


class TestStage2AIClassifier:
    """Test Ollama AI challenge round and dynamic timeout manager."""

    def test_dynamic_timeout_manager_bounds(self):
        mgr = DynamicTimeoutManager()
        t_short = mgr.compute_timeout(prompt_chars=100)
        t_long = mgr.compute_timeout(prompt_chars=10000)

        assert t_short >= 8.0  # MIN
        assert t_long <= 45.0  # MAX
        assert t_long >= t_short

    @pytest.mark.asyncio
    async def test_classify_with_challenge_gambling_verdict(self):
        mock_analyst_response = {
            "verdict": "gambling",
            "confidence": 0.95,
            "category": "casino",
            "reason": "Real money sports betting and slots found on page",
            "key_triggers": ["sports betting", "slots"],
        }
        with patch("checking_url.ai_classifier._call_ollama", AsyncMock(return_value=mock_analyst_response)), \
             patch("checking_url.ai_classifier.validate_gambling_verdict", AsyncMock(return_value={"verdict": "gambling", "confidence": 0.95, "reason": "Validator agreed"})):

            result = await classify_with_challenge("<html>...</html>", url="https://betzone.com", matched_keywords=["sports betting", "casino"])
            assert result["verdict"] == "gambling"
            assert "Validator agreed" in result["reason"] or "gambling" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_classify_with_challenge_offline_timeout(self):
        # When Ollama is offline or times out, _call_ollama returns None -> returns unconfirmed fallback
        with patch("checking_url.ai_classifier._call_ollama", AsyncMock(return_value=None)):
            result = await classify_with_challenge("<html>...</html>", url="https://slowsite.com", matched_keywords=["casino"])
            assert result["verdict"] == "unconfirmed"


class TestStage2RunnerPipeline:
    """Integration tests for Stage 2 runner executing across multiple domains."""

    @pytest.mark.asyncio
    async def test_stage2_runner_mixed_batch(self, mock_mongo, valid_screenshot_path, sample_html_gambling, sample_html_regular):
        src_col = mock_mongo["source_domains"]
        chk_col = mock_mongo["checked_domains"]

        # Seed 4 domains
        src_col.insert_many([
            {"_id": "gamblingsite.com", "domain": "gamblingsite.com", "active": True, "processed": False},
            {"_id": "regularsite.com", "domain": "regularsite.com", "active": True, "processed": False},
            {"_id": "blockedsite.com", "domain": "blockedsite.com", "active": True, "processed": False},
            {"_id": "deadsite.com", "domain": "deadsite.com", "active": True, "processed": False},
        ])

        async def mock_fetch_dispatcher(url, domain, **kwargs):
            if "gambling" in domain:
                return FetchResult(url=url, status_code=200, html=sample_html_gambling)
            elif "regular" in domain:
                return FetchResult(url=url, status_code=200, html=sample_html_regular)
            elif "blocked" in domain:
                return FetchResult(url=url, status_code=403, failure_type="blocked", error="403 Forbidden")
            else:
                return FetchResult(url=url, status_code=404, failure_type="dead_confirmed", error="404 Not Found")

        async def mock_capture_url(url, output_dir, **kwargs):
            return (valid_screenshot_path, "success", None)

        with patch("checking_url.runner.fetch", side_effect=mock_fetch_dispatcher), \
             patch("checking_url.runner.BrowserPool.start", AsyncMock()), \
             patch("checking_url.runner.BrowserPool.close", AsyncMock()), \
             patch("checking_url.runner.BrowserPool.capture_url", side_effect=mock_capture_url):

            stats = await check_run(concurrency=2, limit=0, mode="new")

            assert stats["gambling"] == 1
            assert stats["regular"] == 1
            assert stats["blocked"] == 1
            assert stats["dead"] == 1
            assert stats["screenshots_taken"] == 1

            # Check DB documents
            doc_g = chk_col.find_one({"_id": "gamblingsite.com"})
            assert doc_g["status"] == "gambling"
            assert doc_g["screenshot_taken"] is True

            doc_r = chk_col.find_one({"_id": "regularsite.com"})
            assert doc_r["status"] == "regular"
            assert doc_r["screenshot_taken"] is False

            doc_b = chk_col.find_one({"_id": "blockedsite.com"})
            assert doc_b["status"] == "blocked"

            doc_d = chk_col.find_one({"_id": "deadsite.com"})
            assert doc_d["status"] == "dead"

    @pytest.mark.asyncio
    async def test_stage2_runner_skip_already_processed(self, mock_mongo):
        src_col = mock_mongo["source_domains"]
        src_col.insert_one({"_id": "done.com", "domain": "done.com", "active": True, "processed": True})

        with patch("checking_url.runner.BrowserPool.start", AsyncMock()), \
             patch("checking_url.runner.BrowserPool.close", AsyncMock()):
            stats = await check_run(concurrency=2, limit=0, mode="new")
            assert stats == {}  # Nothing to process

    @pytest.mark.asyncio
    async def test_stage2_runner_recheck_blocked_mode(self, mock_mongo, valid_screenshot_path, sample_html_gambling):
        chk_col = mock_mongo["checked_domains"]
        # Domain previously marked blocked
        chk_col.insert_one({"_id": "unblocked.com", "domain": "unblocked.com", "status": "blocked", "screenshot_taken": False})

        with patch("checking_url.runner.fetch", AsyncMock(return_value=FetchResult("https://unblocked.com", 200, sample_html_gambling))), \
             patch("checking_url.runner.BrowserPool.start", AsyncMock()), \
             patch("checking_url.runner.BrowserPool.close", AsyncMock()), \
             patch("checking_url.runner.BrowserPool.capture_url", AsyncMock(return_value=(valid_screenshot_path, "success", None))):

            stats = await check_run(concurrency=2, limit=0, mode="blocked")
            assert stats["gambling"] == 1

            doc = chk_col.find_one({"_id": "unblocked.com"})
            assert doc["status"] == "gambling"
            assert doc["screenshot_taken"] is True
