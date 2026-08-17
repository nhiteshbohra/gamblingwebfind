"""
tests/test_api.py — FastAPI endpoint tests using TestClient against isolated mock environment.
"""
import os
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from api.main import app
from capture_url.screenshot import _url_to_filename
import api.jobs as jobs


@pytest.fixture
def client():
    return TestClient(app)


class TestAPIDomains:
    """Test /api/domains, /api/domains/{domain}, and /api/domains/{domain}/screenshot endpoints."""

    def test_list_domains_filtering_and_pagination(self, client, mock_mongo):
        chk_col = mock_mongo["checked_domains"]
        chk_col.insert_many([
            {"_id": "betzone1.com", "domain": "betzone1.com", "status": "gambling", "screenshot_taken": True, "added_date": "2026-08-01"},
            {"_id": "betzone2.com", "domain": "betzone2.com", "status": "gambling", "screenshot_taken": False, "added_date": "2026-08-02"},
            {"_id": "regularnews.org", "domain": "regularnews.org", "status": "regular", "screenshot_taken": False, "added_date": "2026-08-03"},
            {"_id": "blockedpage.xyz", "domain": "blockedpage.xyz", "status": "blocked", "screenshot_taken": False, "added_date": "2026-08-04"},
        ])

        # 1. Status filter
        resp = client.get("/api/domains?status=gambling")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["results"]) == 2

        # 2. Search query filter
        resp_q = client.get("/api/domains?q=news")
        assert resp_q.status_code == 200
        data_q = resp_q.json()
        assert data_q["total"] == 1
        assert data_q["results"][0]["_id"] == "regularnews.org"

        # 3. Screenshot filter
        resp_ss = client.get("/api/domains?screenshot=taken")
        assert resp_ss.status_code == 200
        assert resp_ss.json()["total"] == 1

        # 4. Pagination
        resp_page = client.get("/api/domains?page=1&per_page=2")
        assert resp_page.status_code == 200
        assert len(resp_page.json()["results"]) == 2

    def test_get_domain_by_id(self, client, mock_mongo):
        chk_col = mock_mongo["checked_domains"]
        chk_col.insert_one({"_id": "findme.com", "domain": "findme.com", "status": "gambling"})

        # Found
        resp = client.get("/api/domains/findme.com")
        assert resp.status_code == 200
        assert resp.json()["_id"] == "findme.com"

        # Not Found
        resp_404 = client.get("/api/domains/notfound.com")
        assert resp_404.status_code == 404

    def test_get_screenshot_file_serving(self, client, mock_mongo, valid_screenshot_path, isolated_env):
        chk_col = mock_mongo["checked_domains"]
        shots_dir = isolated_env["screenshot_dir"]

        shutil.copy2(valid_screenshot_path, shots_dir / _url_to_filename("https://shotdomain.com"))
        chk_col.insert_one({
            "_id": "shotdomain.com",
            "domain": "shotdomain.com",
            "url": "https://shotdomain.com",
            "status": "gambling",
            "screenshot_taken": True,
        })

        # Valid screenshot served
        resp = client.get("/api/domains/shotdomain.com/screenshot")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

        # Missing screenshot -> 404
        resp_missing = client.get("/api/domains/missingdomain.com/screenshot")
        assert resp_missing.status_code == 404


class TestAPIPipelineRunsAndJobs:
    """Test job lifecycle, stage triggering, conflict handling, and SSE log streaming."""

    def test_run_stage_and_conflict_handling(self, client):
        # Reset any running jobs in jobs dictionary
        jobs._jobs.clear()

        with patch("api.routers.pipeline._run_keywords", MagicMock()):
            # Trigger keywords job
            resp = client.post("/api/run/keywords")
            assert resp.status_code == 200
            job_id = resp.json()["job_id"]
            assert job_id is not None

            # Check /api/status
            resp_status = client.get("/api/status")
            assert resp_status.status_code == 200
            assert any(j["job_id"] == job_id for j in resp_status.json())

            # Triggering again while running -> 409 Conflict
            resp_conflict = client.post("/api/run/keywords")
            assert resp_conflict.status_code == 409

            # Mark job done
            jobs.finish_job(job_id, "done")
            assert jobs.is_stage_running("keywords") is False

    def test_stop_stage_and_all_endpoints(self, client):
        jobs._jobs.clear()
        with patch("api.routers.pipeline._run_keywords", MagicMock()):
            resp = client.post("/api/run/keywords")
            assert resp.status_code == 200
            job_id = resp.json()["job_id"]
            assert jobs.is_stage_running("keywords") is True

            # Stop specific stage
            resp_stop = client.post("/api/stop/keywords")
            assert resp_stop.status_code == 200
            assert resp_stop.json()["stopped"] is True
            assert jobs.is_stage_running("keywords") is False

            # Test stopping specific job_id
            job_id_2 = jobs.new_job("check")
            resp_stop_job = client.post(f"/api/jobs/{job_id_2}/stop")
            assert resp_stop_job.status_code == 200
            assert resp_stop_job.json()["status"] == "stopped"

            # Test stop all
            jobs.new_job("export")
            jobs.new_job("screenshot")
            resp_stop_all = client.post("/api/stop")
            assert resp_stop_all.status_code == 200
            assert resp_stop_all.json()["stopped_count"] == 2

    def test_import_endpoint(self, client, mock_mongo):
        chk_col = mock_mongo["checked_domains"]

        # Valid import payload
        payload = {"domains": ["https://api-imported1.com/play", "api-imported2.in"]}
        resp = client.post("/api/run/import", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["inserted"] == 2
        assert chk_col.count_documents({"status": "gambling"}) == 2

        # Empty payload -> 400 Bad Request
        resp_bad = client.post("/api/run/import", json={"domains": []})
        assert resp_bad.status_code == 400


class TestAPIReportsAndSettingsAndStats:
    """Test reports listing/download, settings healthcheck, and aggregated statistics."""

    def test_reports_listing_and_download(self, client, tmp_path):
        out_dir = Path("output")
        out_dir.mkdir(parents=True, exist_ok=True)
        report_file = out_dir / "test_report.xlsx"
        report_file.write_text("sample content")

        resp = client.get("/api/reports")
        assert resp.status_code == 200
        reports = resp.json()
        assert any(r["name"] == "test_report.xlsx" for r in reports)

        # Download report
        resp_dl = client.get("/api/reports/download/test_report.xlsx")
        assert resp_dl.status_code == 200

        # Path traversal protection / missing file -> 404
        resp_traversal = client.get("/api/reports/download/../../invalid.txt")
        assert resp_traversal.status_code == 404

        # Clean up
        if report_file.exists():
            report_file.unlink()

    def test_settings_endpoint(self, client):
        with patch("urllib.request.urlopen", MagicMock()):
            resp = client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json()
            assert data["mongo"]["ok"] is True
            assert "ollama" in data
            assert "config" in data

    def test_stats_endpoint_aggregation(self, client, mock_mongo):
        src_col = mock_mongo["source_domains"]
        chk_col = mock_mongo["checked_domains"]

        src_col.insert_many([
            {"_id": "s1.com", "domain": "s1.com", "active": True, "processed": True, "source": "searxng_search"},
            {"_id": "s2.com", "domain": "s2.com", "active": True, "processed": False, "source": "searxng_search"},
        ])
        chk_col.insert_many([
            {"_id": "c1.com", "domain": "c1.com", "status": "gambling", "screenshot_taken": True, "exported": False},
            {"_id": "c2.com", "domain": "c2.com", "status": "regular", "screenshot_taken": False},
        ])

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        stats = resp.json()

        assert stats["source_domains"]["active"] == 2
        assert stats["source_domains"]["pending"] == 1
        assert stats["checked_domains"]["gambling"] == 1
        assert stats["checked_domains"]["regular"] == 1
        assert stats["checked_domains"]["screenshot_taken"] == 1
        assert stats["checked_domains"]["pending_export"] == 1
