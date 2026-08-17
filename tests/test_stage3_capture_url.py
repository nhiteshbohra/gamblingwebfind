"""
tests/test_stage3_capture_url.py — Unit and integration tests for Stage 3 screenshot validation, report generation, and Excel export.
"""
import os
import shutil
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from capture_url.screenshot import (
    _url_to_filename,
    is_valid_screenshot,
    delete_screenshot,
)
from capture_url.excel_exporter import export_capture_workbook
from capture_url.docx_report_generator import build_report_from_mongo
from capture_url.runner import run as capture_run
from main import _size_based_split
from db.mongo_client import checked_domains


class TestStage3ScreenshotValidation:
    """Test filename hashing and image validity assertions."""

    def test_url_to_filename(self):
        fn = _url_to_filename("https://www.casinoking.com/play")
        assert fn.endswith(".jpg")
        assert "casinoking_com" in fn

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


class TestStage3ExcelExporter:
    """Test Excel workbook generation in single and batched modes."""

    def test_export_single_combined_excel(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        # Place valid screenshot in SCREENSHOT_DIR
        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://exbet1.com"))

        chk_col.insert_one({
            "_id": "exbet1.com",
            "domain": "exbet1.com",
            "url": "https://exbet1.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        res = export_capture_workbook(
            domain_ids=["exbet1.com"],
            output_dir=str(tmp_path / "output"),
            single_file=True,
        )

        assert res["captured"] == 1
        assert len(res["files"]) >= 1
        assert any("captured_domains_combined.xlsx" in f for f in res["files"])

        doc = chk_col.find_one({"_id": "exbet1.com"})
        assert doc["exported"] is True

    def test_export_batched_excel(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        domain_ids = [f"batchbet{i}.com" for i in range(1, 8)]  # 7 domains
        for d in domain_ids:
            shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename(f"https://{d}"))
            chk_col.insert_one({
                "_id": d,
                "domain": d,
                "url": f"https://{d}",
                "status": "gambling",
                "screenshot_taken": True,
                "exported": False,
            })

        # Batch size of 3 with 7 domains -> should produce 3 batches (3 + 3 + 1)
        res = export_capture_workbook(
            domain_ids=domain_ids,
            output_dir=str(tmp_path / "output"),
            batch_size=3,
            single_file=False,
        )

        assert res["captured"] == 7
        assert res["batches"] == 3
        assert len(res["files"]) == 3


class TestStage3DocxAndPdfReportGenerator:
    """Test Word and PDF report generation."""

    def test_build_report_from_mongo_single_file(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://docbet.com"))

        chk_col.insert_one({
            "_id": "docbet.com",
            "domain": "docbet.com",
            "url": "https://docbet.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        res = build_report_from_mongo(
            domain_ids=["docbet.com"],
            output_dir=str(tmp_path / "output"),
            single_file=True,
            pdf=False,  # Skip COM PDF in unit test
        )

        assert len(res["docx_paths"]) == 1
        assert os.path.exists(res["docx_paths"][0])

    def test_build_report_zero_domains_handled_gracefully(self, mock_mongo, tmp_path):
        res = build_report_from_mongo(
            domain_ids=[],
            output_dir=str(tmp_path / "output"),
            single_file=True,
            pdf=False,
        )
        assert res == {"docx_paths": [], "pdf_paths": []}


class TestStage3SizeBasedSplitAndRunner:
    """Test size-based batching and capture runner."""

    def test_size_based_split_logic(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        domain_ids = [f"sizedomain{i}.com" for i in range(1, 6)]
        for d in domain_ids:
            shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename(f"https://{d}"))
            chk_col.insert_one({
                "_id": d,
                "url": f"https://{d}",
                "status": "gambling",
                "screenshot_taken": True,
            })

        # Set a tiny limit (0.0001 MB ~ 100 bytes) to force every domain into its own batch
        batches = _size_based_split(domain_ids, pdf_limit_mb=0.0001)
        assert len(batches) == len(domain_ids)

    @pytest.mark.asyncio
    async def test_capture_runner_verified_and_missing_recovery(self, mock_mongo, valid_screenshot_path, isolated_env, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        # Domain 1 has valid screenshot on disk
        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://present.com"))
        chk_col.insert_one({
            "_id": "present.com",
            "domain": "present.com",
            "url": "https://present.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        # Domain 2 is marked screenshot_taken=True but file is missing on disk
        chk_col.insert_one({
            "_id": "missing.com",
            "domain": "missing.com",
            "url": "https://missing.com",
            "status": "gambling",
            "screenshot_taken": True,
            "exported": False,
        })

        # Mock screenshot_runner to simulate re-capturing missing.com
        async def mock_ss_run(domain_ids=None, **kwargs):
            shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://missing.com"))
            return {"captured": 1, "failed": 0, "total": 1}

        with patch("capture_url.screenshot_runner.run", side_effect=mock_ss_run):
            verified = await capture_run(concurrency=2, limit=0)
            assert "present.com" in verified
            assert "missing.com" in verified
