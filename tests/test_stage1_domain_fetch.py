"""
tests/test_stage1_domain_fetch.py — Domain extraction, URL normalization, and Stage 1 ingestion tests.
"""
import os
import tempfile
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from db.mongo_client import (
    extract_domain,
    normalize_url,
    extract_domains_from_file,
    seed_from_csv,
    seed_discovered_domains,
    source_domains,
)


class TestStage1DomainExtractionAndNormalization:
    """Test URL parsing, normalization, and tldextract domain extraction."""

    def test_extract_domain_varieties(self):
        # Standard domains
        assert extract_domain("https://example.com/path") == "example.com"
        assert extract_domain("http://www.example.com") == "example.com"
        # Subdomains
        assert extract_domain("https://live.sports.betking.co.uk/games") == "betking.co.uk"
        assert extract_domain("http://promo.casinoroyale.bet") == "casinoroyale.bet"
        # Ports
        assert extract_domain("https://matka-play.in:8443/home") == "matka-play.in"

    def test_normalize_url(self):
        # Strips www, ports, trailing slashes on subpaths
        assert normalize_url("http://www.testcasino.com:80/play/") == "http://testcasino.com/play"
        assert normalize_url("https://www.testcasino.com:443/") == "https://testcasino.com/"
        assert normalize_url("http://testcasino.com/login/") == "http://testcasino.com/login"

    def test_extract_domains_from_text_file(self, tmp_path):
        txt_path = tmp_path / "raw_domains.txt"
        txt_path.write_text(
            "# Comment line\n"
            "https://www.site1.com/home\n"
            "site2.in\n"
            "   site1.com   \n"  # Duplicate with whitespace
            "\n"
            "http://sub.site3.org:8080/path\n",
            encoding="utf-8"
        )

        extracted = extract_domains_from_file(str(txt_path))
        assert extracted == ["site1.com", "site2.in", "site3.org"]

    def test_extract_domains_from_csv_and_excel(self, tmp_path):
        # CSV
        csv_path = tmp_path / "domains.csv"
        csv_path.write_text("Header1,Header2\nhttps://csvdomain1.com,some text\ncsvdomain2.org,other\n", encoding="utf-8")
        assert extract_domains_from_file(str(csv_path)) == ["csvdomain1.com", "csvdomain2.org"]

        # Excel (.xlsx)
        xlsx_path = tmp_path / "domains.xlsx"
        df = pd.DataFrame({"URLs": ["https://excel1.com/bonus", "https://excel2.net"]})
        df.to_excel(xlsx_path, index=False)
        extracted = extract_domains_from_file(str(xlsx_path))
        assert "excel1.com" in extracted
        assert "excel2.net" in extracted


class TestStage1DatabaseSeeding:
    """Test seeding domain_Listed from CSV and discovered sources (Common Crawl / Deep Crawl)."""

    def test_seed_from_csv(self, tmp_path, mock_mongo):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(
            "domain,note\n"
            "seeddomain1.com,trusted\n"
            "seeddomain2.in,candidate\n"
            ",empty\n",
            encoding="utf-8"
        )

        seed_from_csv(str(csv_path), active=True)
        col = mock_mongo["source_domains"]
        assert col.count_documents({}) == 2

        doc = col.find_one({"_id": "seeddomain1.com"})
        assert doc["domain"] == "seeddomain1.com"
        assert doc["active"] is True
        assert "added_date" in doc

    def test_seed_discovered_domains_with_source(self, mock_mongo):
        col = mock_mongo["source_domains"]

        # First batch
        inserted, skipped = seed_discovered_domains(
            {"crawldomain1.com", "crawldomain2.com"},
            discovered_from="common_crawl"
        )
        assert inserted == 2
        assert skipped == 0

        doc = col.find_one({"_id": "crawldomain1.com"})
        assert doc["source"] == "deep_crawl"
        assert doc["discovered_from"] == "common_crawl"
        assert doc["processed"] is False

        # Attempt to insert same domain again -> should skip, not overwrite
        inserted2, skipped2 = seed_discovered_domains(
            {"crawldomain1.com", "crawldomain3.com"},
            discovered_from="common_crawl_run2"
        )
        assert inserted2 == 1
        assert skipped2 == 1

        # Original discovered_from is preserved
        doc1 = col.find_one({"_id": "crawldomain1.com"})
        assert doc1["discovered_from"] == "common_crawl"

    def test_common_crawl_mock_fetch_and_seed(self, mock_mongo):
        """Simulate Common Crawl index parsing and domain ingestion."""
        # Simulated Common Crawl index WARC/CDX records
        mock_cdx_records = [
            {"url": "https://www.cc-casino1.com/game?id=99", "status": "200"},
            {"url": "https://cc-betting2.org/sports", "status": "200"},
            {"url": "https://sub.cc-casino1.com/login", "status": "200"},  # Duplicate root domain
            {"url": "https://invalid-ip-record/path", "status": "200"},
        ]

        extracted_domains = set()
        for record in mock_cdx_records:
            d = extract_domain(record["url"])
            if d and "." in d:
                extracted_domains.add(d)

        inserted, skipped = seed_discovered_domains(extracted_domains, discovered_from="common_crawl_warc")
        assert inserted == 2
        assert mock_mongo["source_domains"].count_documents({"_id": "cc-casino1.com"}) == 1
        assert mock_mongo["source_domains"].count_documents({"_id": "cc-betting2.org"}) == 1
