"""
tests/test_stage3_export_domains.py — Unit and integration tests for Stage 3 screenshot validation, report generation, and Excel export.
"""
import os
import shutil
from pathlib import Path
import pytest
import openpyxl
from unittest.mock import patch

from export_domains.screenshot import (
    _url_to_filename,
    is_valid_screenshot,
    delete_screenshot,
    all_filename_candidates,
)
from export_domains.exporter import (
    run_export,
    run as export_run,
    build_workbook,
    build_report,
)


class TestStage3ScreenshotValidation:
    """Test filename hashing and image validity assertions."""

    def test_url_to_filename(self):
        fn = _url_to_filename("https://www.casinoking.com/play")
        assert fn.endswith(".jpg")
        assert "casinoking_com" in fn

    def test_all_filename_candidates(self):
        cands = all_filename_candidates("https://casinoking.com/play", "casinoking.com")
        assert _url_to_filename("https://casinoking.com/play") in cands
        assert _url_to_filename("https://casinoking.com") in cands
        assert _url_to_filename("http://casinoking.com") in cands
        assert _url_to_filename("https://www.casinoking.com") in cands
        assert _url_to_filename("http://www.casinoking.com") in cands

        # Domain with www prefix should not produce www.www.
        cands_www = all_filename_candidates("https://www.casinoking.com", "www.casinoking.com")
        for c in cands_www:
            assert "www_www" not in c

    def test_is_valid_screenshot(self, valid_screenshot_path, corrupt_screenshot_path, tmp_path):
        assert is_valid_screenshot(valid_screenshot_path) is True
        assert is_valid_screenshot(corrupt_screenshot_path) is False
        assert is_valid_screenshot(str(tmp_path / "non_existent.jpg")) is False

    def test_delete_screenshot(self, tmp_path, valid_screenshot_path):
        shots_dir = tmp_path / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        cand_name = _url_to_filename("https://testdel.com")
        target_file = shots_dir / cand_name
        shutil.copy2(valid_screenshot_path, target_file)

        assert target_file.exists()
        deleted = delete_screenshot("https://testdel.com", output_dir=str(shots_dir))
        assert deleted is True
        assert not target_file.exists()

    def test_size_based_split_finds_www_screenshot(self, mock_mongo, tmp_path, valid_screenshot_path, monkeypatch):
        from main import _size_based_split
        shots_dir = tmp_path / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("SCREENSHOT_DIR", str(shots_dir))

        # Save screenshot under www fallback name
        www_filename = _url_to_filename("https://www.fallbacksport.com")
        shutil.copy2(valid_screenshot_path, shots_dir / www_filename)
        file_size = os.path.getsize(shots_dir / www_filename)

        mock_mongo["checked_domains"].insert_one({
            "_id": "fallbacksport.com",
            "url": "https://fallbacksport.com",
            "status": "gambling",
        })

        # When limit is tiny, single entry with screenshot must split if it exceeds limit
        limit_mb = (file_size + 12000) / (1024 * 1024 * 0.90)
        batches = _size_based_split(["fallbacksport.com"], pdf_limit_mb=limit_mb)
        assert len(batches) == 1
        assert batches[0] == ["fallbacksport.com"]


class TestStage3ExcelBuilder:
    """Test 2-sheet Excel workbook generation."""

    def test_build_workbook_two_sheets(self, tmp_path):
        out_xlsx = str(tmp_path / "report.xlsx")
        entries = [
            {"domain": "betwin1.com", "url": "https://betwin1.com", "screenshot_path": "dummy.jpg"},
            {"domain": "betwin2.com", "url": "https://betwin2.com", "screenshot_path": "dummy2.jpg"},
        ]
        failed_docs = [
            {"domain": "deadbet.com", "url": "https://deadbet.com", "screenshot_failed_reason": "Timeout"},
        ]

        result_path = build_workbook(entries, failed_docs, out_xlsx)
        assert os.path.exists(result_path)

        wb = openpyxl.load_workbook(result_path)
        assert "Captured Domains" in wb.sheetnames
        assert "Failed Domains" in wb.sheetnames

        ws_cap = wb["Captured Domains"]
        assert ws_cap.max_row == 3  # Header + 2 rows

        ws_fail = wb["Failed Domains"]
        assert ws_fail.max_row == 2  # Header + 1 row


class TestStage3DocxBuilder:
    """Test Word document construction."""

    def test_build_report_docx(self, valid_screenshot_path, tmp_path):
        out_docx = str(tmp_path / "report.docx")
        entries = [
            {"domain": "docbet.com", "url": "https://docbet.com", "screenshot_path": valid_screenshot_path},
        ]

        res = build_report(entries, out_docx)
        assert os.path.exists(res)
        assert os.path.getsize(res) > 1000


class TestStage3UnifiedExportPipeline:
    """Test the single-command export pipeline."""

    def test_run_export_end_to_end(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        # Seed 2 gambling domains: 1 with valid screenshot, 1 missing
        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://gamble1.com"))

        chk_col.insert_one({
            "_id": "gamble1.com",
            "domain": "gamble1.com",
            "url": "https://gamble1.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })
        chk_col.insert_one({
            "_id": "gamble_missing.com",
            "domain": "gamble_missing.com",
            "url": "https://gamble_missing.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        # Mock PDF conversion in test environment (COM may not be available on all test runners)
        with patch("export_domains.exporter._convert_to_pdf", return_value=str(tmp_path / "report.pdf")):
            result = run_export(domain_ids=["gamble1.com", "gamble_missing.com"])

        assert result["captured"] == 1
        assert result["failed"] == 1
        assert result["xlsx"] is not None
        assert os.path.exists(result["xlsx"])

        # Check DB updates
        doc1 = chk_col.find_one({"_id": "gamble1.com"})
        assert doc1["exported"] is True

        doc2 = chk_col.find_one({"_id": "gamble_missing.com"})
        assert doc2["screenshot_taken"] is False

    @pytest.mark.asyncio
    async def test_async_runner(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://asyncgamble.com"))
        chk_col.insert_one({
            "_id": "asyncgamble.com",
            "domain": "asyncgamble.com",
            "url": "https://asyncgamble.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        with patch("export_domains.exporter._convert_to_pdf", return_value=str(tmp_path / "report.pdf")):
            result = await export_run()

        assert result["captured"] == 1
        assert result["failed"] == 0


class TestStage3BatchSplitter:
    """Test export_domains/batch_splitter.py create_batches."""

    def test_export_domains_create_batches(self, tmp_path):
        import csv
        import pymupdf
        from export_domains.batch_splitter import create_batches

        csv_path = tmp_path / "report.csv"
        pdf_path = tmp_path / "report.pdf"
        output_root = tmp_path / "Batches"

        # Create sample CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Domain", "URL"])
            for i in range(4):
                w.writerow([f"site{i}.com", f"https://site{i}.com"])

        # Create sample PDF with PyMuPDF
        doc = pymupdf.open()
        for i in range(4):
            page = doc.new_page(width=300, height=300)
            page.draw_rect(pymupdf.Rect(10, 10, 200, 200), color=(1, 0, 0), fill=(0, 1, 0))
            page.insert_text((20, 50), f"Page {i+1} content")
        doc.save(str(pdf_path))
        doc.close()

        batches = create_batches(
            csv_path=str(csv_path),
            pdf_path=str(pdf_path),
            output_root=str(output_root),
            limit_mb=0.01,
        )

        assert len(batches) >= 1
        assert os.path.exists(output_root)
        for b in batches:
            assert os.path.isdir(b)
            assert any(f.endswith(".xlsx") for f in os.listdir(b))
            assert any(f.endswith(".pdf") for f in os.listdir(b))
