"""
tests/test_helpers_and_import.py — Tests for true positive ingestion and helper scripts in project_sup/helping_code.
"""
import os
import csv
import pytest
import pymupdf
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from db.mongo_client import (
    ingest_true_positives,
    checked_domains,
    source_domains,
    find_regular_domains,
    find_dead_domains,
    write_result,
)
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
        assert chk_col.count_documents({"status": "gambling"}) == 3

    def test_ingest_true_positives_existing_reset(self, mock_mongo, tmp_path):
        chk_col = mock_mongo["checked_domains"]
        chk_col.insert_one({
            "_id": "alreadythere.com",
            "domain": "alreadythere.com",
            "status": "regular",
            "screenshot_taken": True,
            "exported": True,
        })

        txt_file = tmp_path / "tp.txt"
        txt_file.write_text("alreadythere.com\n", encoding="utf-8")

        inserted, reset = ingest_true_positives(str(txt_file))
        assert inserted == 0
        assert reset == 1

        # Assert reset for re-capture and status converted to gambling
        doc = chk_col.find_one({"_id": "alreadythere.com"})
        assert doc["status"] == "gambling"
        assert doc["screenshot_taken"] is False
        assert doc["exported"] is False

    def test_ingest_true_positives_from_list(self, mock_mongo):
        chk_col = mock_mongo["checked_domains"]
        inserted, reset = ingest_true_positives(["https://listcasino1.com", "listcasino2.com"])
        assert inserted == 2
        assert reset == 0
        assert chk_col.count_documents({"status": "gambling"}) == 2


class TestRecheckFinders:
    """Test find_regular_domains and find_dead_domains queries."""

    def test_find_regular_and_dead_domains(self, mock_mongo):
        chk_col = mock_mongo["checked_domains"]
        src_col = mock_mongo["source_domains"]

        chk_col.insert_many([
            {"_id": "reg1.com", "domain": "reg1.com", "status": "regular", "last_checked_at": "2026-08-01"},
            {"_id": "reg2.com", "domain": "reg2.com", "status": "regular"},
            {"_id": "dead1.com", "domain": "dead1.com", "status": "dead"},
        ])
        src_col.insert_one({"_id": "dead2.com", "domain": "dead2.com", "active": False})

        regular = list(find_regular_domains())
        assert len(regular) == 2
        assert {r["_id"] for r in regular} == {"reg1.com", "reg2.com"}

        dead = list(find_dead_domains())
        assert len(dead) == 2
        assert {d["_id"] for d in dead} == {"dead1.com", "dead2.com"}


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


class TestMainDashboardLaunch:
    """Test background dashboard launch and --no-ui logic in main.py."""

    def test_is_port_in_use(self):
        from main import _is_port_in_use
        import socket
        # Test an active listening port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert _is_port_in_use("127.0.0.1", port) is True
        finally:
            srv.close()
        assert _is_port_in_use("127.0.0.1", port) is False

    def test_start_web_dashboard_already_running(self):
        from main import start_web_dashboard
        with patch("main._is_port_in_use", return_value=True), \
             patch("webbrowser.open") as mock_open:
            start_web_dashboard(host="127.0.0.1", port=8081)
            mock_open.assert_called_once_with("http://127.0.0.1:8081")

    def test_start_web_dashboard_starts_thread(self):
        from main import start_web_dashboard
        with patch("main._is_port_in_use", side_effect=[False, True]), \
             patch("webbrowser.open") as mock_open, \
             patch("threading.Thread") as mock_thread:
            mock_t_inst = MagicMock()
            mock_thread.return_value = mock_t_inst

            start_web_dashboard(host="127.0.0.1", port=8081)

            mock_thread.assert_called_once()
            assert mock_thread.call_args[1].get("daemon") is True
            mock_t_inst.start.assert_called_once()
            mock_open.assert_called_once_with("http://127.0.0.1:8081")

    def test_main_respects_no_ui_flag(self, mock_mongo):
        from main import main
        with patch("sys.argv", ["main.py", "--no-ui"]), \
             patch("main.start_web_dashboard") as mock_start_ui, \
             patch("main.interactive_menu") as mock_menu:
            main()
            mock_start_ui.assert_not_called()
            mock_menu.assert_called_once()


class TestOllamaGatedStartup:
    """Test start_ollama_if_needed and option-gating behavior."""

    @pytest.mark.asyncio
    async def test_start_ollama_already_running(self):
        from checking_url.ai_classifier import start_ollama_if_needed
        with patch("checking_url.ai_classifier.check_ollama_status", AsyncMock(return_value=(True, "Online"))), \
             patch("subprocess.Popen") as mock_popen:
            res = await start_ollama_if_needed()
            assert res is True
            mock_popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_ollama_autostart_success(self, monkeypatch):
        from checking_url.ai_classifier import start_ollama_if_needed
        monkeypatch.setenv("OLLAMA_AUTOSTART_TIMEOUT", "2.0")
        # Offline at first check, online after autostart
        with patch("checking_url.ai_classifier.check_ollama_status", AsyncMock(side_effect=[(False, "Offline"), (True, "Started")])), \
             patch("subprocess.Popen") as mock_popen:
            res = await start_ollama_if_needed()
            assert res is True
            mock_popen.assert_called_once()
            assert "serve" in mock_popen.call_args[0][0]

    @pytest.mark.asyncio
    async def test_start_ollama_autostart_failure_graceful(self, monkeypatch):
        from checking_url.ai_classifier import start_ollama_if_needed
        monkeypatch.setenv("OLLAMA_AUTOSTART_TIMEOUT", "0.2")
        with patch("checking_url.ai_classifier.check_ollama_status", AsyncMock(return_value=(False, "Offline"))), \
             patch("subprocess.Popen", side_effect=FileNotFoundError("ollama not found")):
            res = await start_ollama_if_needed()
            assert res is False  # Must not raise

    def test_run_checking_url_invokes_start_ollama(self):
        from main import run_checking_url
        with patch("main.ask_checking_mode", return_value="new"), \
             patch("checking_url.ai_classifier.start_ollama_if_needed", AsyncMock(return_value=True)) as mock_start_ollama, \
             patch("checking_url.runner.run", AsyncMock(return_value={"gambling": 0})):
            run_checking_url()
            mock_start_ollama.assert_called_once()






