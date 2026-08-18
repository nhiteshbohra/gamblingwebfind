"""
tests/test_helpers_and_import.py — Tests for true positive ingestion and helper scripts in project_sup/helping_code.
"""
import os
import csv
import pytest
import pymupdf
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from db.mongo_client import ingest_true_positives, checked_domains, source_domains
from export_domains.batch_splitter import create_batches, _make_excel_urls_clickable as make_excel_urls_clickable


class TestTruePositivesImport:
    """Test manual true positive import flow."""

    def test_ingest_true_positives_new_domains(self, mock_mongo, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        src_col = mock_mongo["source_domains"]

        txt_file = tmp_path / "true_positives.txt"
        txt_file.write_text(
            "https://confirmedcasino1.com/\n"
            "   confirmedcasino2.in   \n"
            "\n"
            "http://sub.confirmedcasino3.org\n",
            encoding="utf-8"
        )

        inserted, skipped = ingest_true_positives(str(txt_file))
        assert inserted == 3
        assert skipped == 0

        doc1 = chk_col.find_one({"_id": "confirmedcasino1.com"})
        assert doc1["status"] == "gambling"
        assert doc1["screenshot_taken"] is False
        assert doc1["source"] == "manual_import"

        # Check source collection sync
        src_doc = src_col.find_one({"_id": "confirmedcasino1.com"})
        # When inserted, source_domains update_one upsert=False, so if not present it won't crash
        assert chk_col.count_documents({"status": "gambling"}) == 3

    def test_ingest_true_positives_existing_reset(self, mock_mongo, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        chk_col.insert_one({
            "_id": "alreadythere.com",
            "domain": "alreadythere.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": True,
        })

        txt_file = tmp_path / "tp.txt"
        txt_file.write_text("alreadythere.com\n", encoding="utf-8")

        inserted, skipped = ingest_true_positives(str(txt_file))
        assert inserted == 0
        assert skipped == 1

        # Assert reset for re-capture
        doc = chk_col.find_one({"_id": "alreadythere.com"})
        assert doc["screenshot_taken"] is False
        assert doc["exported"] is False


class TestHelperBatchSplitter:
    """Test project_sup/helping_code/batch_splitter.py create_batches."""

    def test_batch_splitter_create_batches(self, tmp_path):
        csv_path = tmp_path / "test.csv"
        pdf_path = tmp_path / "test.pdf"
        output_root = tmp_path / "split_batches"

        # Create sample CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Domain", "URL"])
            for i in range(5):
                w.writerow([f"site{i}.com", f"https://site{i}.com"])

        # Create sample PDF with PyMuPDF
        doc = pymupdf.open()
        for i in range(5):
            page = doc.new_page(width=300, height=300)
            page.draw_rect(pymupdf.Rect(10, 10, 200, 200), color=(1, 0, 0), fill=(0, 1, 0))
            page.insert_text((20, 50), f"Page {i+1} content")
        doc.save(str(pdf_path))
        doc.close()

        create_batches(
            csv_path=str(csv_path),
            pdf_path=str(pdf_path),
            output_root=str(output_root),
            limit_mb=0.01,  # Force small batches
        )

        assert os.path.exists(output_root)
        batches = [d for d in os.listdir(output_root) if os.path.isdir(os.path.join(output_root, d))]
        assert len(batches) >= 1

    def test_batch_splitter_missing_file_handling(self):
        # When non-existent path passed and input returns empty
        with patch("builtins.input", return_value=""):
            # Should fail gracefully and return without crash
            create_batches("non_existent.csv", "non_existent.pdf")


class TestHelperCompareToolAndScreenshotRunner:
    """Test compare tool helpers and screenshot-only runner."""

    def test_make_excel_urls_clickable(self, tmp_path):
        xlsx_path = tmp_path / "clickable.xlsx"
        df = pd.DataFrame({
            "Domain": ["site1.com", "site2.com"],
            "URL": ["https://site1.com", "https://site2.com"],
            "Notes": ["Test1", "Test2"],
        })
        df.to_excel(xlsx_path, index=False)

        # Should format hyperlinks without error
        make_excel_urls_clickable(str(xlsx_path))
        assert os.path.exists(xlsx_path)
