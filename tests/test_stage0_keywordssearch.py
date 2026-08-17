"""
tests/test_stage0_keywordssearch.py — Unit and integration tests for Stage 0 keyword search.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from keywordssearch.searxng_search import (
    extract_domain,
    save_domains_to_mongo,
    run_search,
    duckduckgo_crawl_keyword,
    yahoo_crawl_keyword,
    _BLOCKLIST,
)


class TestStage0ExtractDomain:
    """Test domain extraction and blocklist filtering."""

    def test_extract_valid_domain(self):
        assert extract_domain("https://www.casino-example.com/slots?ref=123") == "casino-example.com"
        assert extract_domain("http://subdomain.satta-matka.in/") == "satta-matka.in"

    def test_extract_blocklisted_domain(self):
        for block_domain in ["google.com", "duckduckgo.com", "wikipedia.org", "youtube.com"]:
            assert extract_domain(f"https://www.{block_domain}/search?q=test") is None

    def test_extract_invalid_url(self):
        assert extract_domain("not-a-valid-url") is None
        assert extract_domain("") is None


class TestStage0SaveDomainsToMongo:
    """Test saving domains into domain_Listed with idempotency."""

    def test_save_new_domains(self, mock_mongo):
        collection = mock_mongo["source_domains"]
        domains = ["royalbet.com", "superlottery.in", "luckyslots.org"]

        inserted = save_domains_to_mongo(domains, collection)
        assert inserted == 3
        assert collection.count_documents({}) == 3

        doc = collection.find_one({"_id": "royalbet.com"})
        assert doc["domain"] == "royalbet.com"
        assert doc["active"] is True
        assert doc["processed"] is False
        assert "added_date" in doc

    def test_duplicate_domains_not_overwritten(self, mock_mongo):
        collection = mock_mongo["source_domains"]
        # Seed an existing record with custom properties
        collection.insert_one({
            "_id": "existingbet.com",
            "domain": "existingbet.com",
            "active": True,
            "processed": True,
            "custom_note": "preserved",
        })

        # Attempt to insert same domain plus a new one
        inserted = save_domains_to_mongo(["existingbet.com", "brandnewbet.com"], collection)
        assert inserted == 1

        # Check preserved record
        doc = collection.find_one({"_id": "existingbet.com"})
        assert doc["processed"] is True  # preserved, not overwritten to False
        assert doc["custom_note"] == "preserved"

    def test_save_empty_list(self, mock_mongo):
        collection = mock_mongo["source_domains"]
        inserted = save_domains_to_mongo([], collection)
        assert inserted == 0


class TestStage0SearchEnginesAndRunSearch:
    """Test search engine crawling and run_search pipeline."""

    @pytest.mark.asyncio
    async def test_duckduckgo_crawl_keyword_mocked(self):
        html_response = """
        <html><body>
            <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.mockbet888.com%2Fhome">link</a>
            <a class="result__a" href="https://www.casinoking99.net/">link2</a>
        </body></html>
        """
        with patch("keywordssearch.searxng_search._ddg_get_sync", return_value=(200, html_response)), \
             patch("asyncio.sleep", AsyncMock()):
            mock_session = MagicMock()
            urls = await duckduckgo_crawl_keyword("online casino", mock_session, delay=0.0)
            assert any("mockbet888.com" in u for u in urls) or any("casinoking99.net" in u for u in urls)

    @pytest.mark.asyncio
    async def test_yahoo_crawl_keyword_mocked(self):
        html_response = """
        <ol class="searchCenterMiddle">
            <li><div class="compTitle"><h3><a href="https://r.search.yahoo.com/_ylt=123/RU=https%3a%2f%2fwww.yahoobet77.com%2f/RK=2">Yahoo Result</a></h3></div></li>
        </ol>
        """
        with patch("keywordssearch.searxng_search._yahoo_get_sync", return_value=(200, html_response)), \
             patch("asyncio.sleep", AsyncMock()):
            urls = await yahoo_crawl_keyword("roulette live", max_pages_per_var=1)
            assert any("yahoobet77.com" in u for u in urls)

    @pytest.mark.asyncio
    async def test_run_search_empty_keywords(self):
        result = await run_search([])
        assert result == {"total_urls": 0, "unique_domains": 0, "new_inserted": 0}

    @pytest.mark.asyncio
    async def test_run_search_happy_path(self, mock_mongo):
        with patch("keywordssearch.searxng_search.USE_PLAYWRIGHT", True), \
             patch("keywordssearch.searxng_search.USE_DUCKDUCKGO", True), \
             patch("keywordssearch.searxng_search.USE_SEARXNG", False), \
             patch("keywordssearch.searxng_search.duckduckgo_crawl_keyword",
                   AsyncMock(return_value=["https://happycasino1.com/play", "https://happycasino2.com/live"])), \
             patch("keywordssearch.searxng_search.playwright_crawl_keyword_sync",
                   return_value=["https://happycasino3.com/slots"]), \
             patch("keywordssearch.searxng_search.yahoo_crawl_keyword",
                   AsyncMock(return_value=[])):

            summary = await run_search(["casino online"])
            assert summary["unique_domains"] >= 3
            assert mock_mongo["source_domains"].count_documents({}) >= 3

    @pytest.mark.asyncio
    async def test_run_search_error_resilience(self, mock_mongo):
        with patch("keywordssearch.searxng_search.USE_PLAYWRIGHT", False), \
             patch("keywordssearch.searxng_search.USE_DUCKDUCKGO", True), \
             patch("keywordssearch.searxng_search.USE_SEARXNG", False), \
             patch("keywordssearch.searxng_search.duckduckgo_crawl_keyword",
                   AsyncMock(side_effect=Exception("DDG Timeout / Network Failure"))), \
             patch("keywordssearch.searxng_search.yahoo_crawl_keyword",
                   AsyncMock(return_value=["https://resilientcasino.com"])):

            summary = await run_search(["unstable query"])
            assert summary["unique_domains"] >= 1
            assert mock_mongo["source_domains"].count_documents({"_id": "resilientcasino.com"}) == 1
