"""
tests/test_end_to_end.py — Full pipeline end-to-end integration test across all stages and API statistics.
"""
import os
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from api.main import app
from checking_url.fetcher import FetchResult
import checking_url.runner as check_runner
from capture_url.runner import run as capture_run
from capture_url.excel_exporter import export_capture_workbook
from capture_url.docx_report_generator import build_report_from_mongo
from capture_url.screenshot import _url_to_filename


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end_lifecycle(
    mock_mongo,
    isolated_env,
    valid_screenshot_path,
    sample_html_gambling,
    sample_html_regular,
):
    """
    End-to-end test verifying full data flow:
    1. Seed 20 test domains into domain_Listed covering all status outcomes.
    2. Execute Stage 2 (check, classify, AI evaluation, immediate screenshot).
    3. Assert exact database distribution in checked_domains and domain_Listed.
    4. Execute Stage 3 (report export to Word and Excel).
    5. Query FastAPI /api/stats and verify 100% precision match with database metrics.
    """
    src_col = mock_mongo["source_domains"]
    chk_col = mock_mongo["checked_domains"]
    shots_dir = isolated_env["screenshot_dir"]
    out_dir = isolated_env["output_dir"]
    client = TestClient(app)

    # ── 1. Seed 20 diverse domains ────────────────────────────────────────────
    # 8 gambling domains (5 direct keyword, 3 AI confirmed)
    # 6 regular domains (4 direct regular, 2 AI regular)
    # 3 blocked domains (403 WAF)
    # 3 dead domains (404 / connection error)
    domain_dataset = []
    for i in range(1, 6):
        domain_dataset.append({"domain": f"keyword-gambling-{i}.com", "type": "keyword_gambling"})
    for i in range(1, 4):
        domain_dataset.append({"domain": f"ai-gambling-{i}.com", "type": "ai_gambling"})
    for i in range(1, 5):
        domain_dataset.append({"domain": f"direct-regular-{i}.com", "type": "direct_regular"})
    for i in range(1, 3):
        domain_dataset.append({"domain": f"ai-regular-{i}.com", "type": "ai_regular"})
    for i in range(1, 4):
        domain_dataset.append({"domain": f"blocked-domain-{i}.com", "type": "blocked"})
    for i in range(1, 4):
        domain_dataset.append({"domain": f"dead-domain-{i}.com", "type": "dead"})

    assert len(domain_dataset) == 20

    src_col.insert_many([
        {
            "_id": item["domain"],
            "domain": item["domain"],
            "active": True,
            "processed": False,
            "source": "e2e_seed",
            "added_date": "2026-08-17",
        }
        for item in domain_dataset
    ])
    assert src_col.count_documents({}) == 20

    # ── 2. Mock Fetcher, AI Classifier, and Browser Screenshot Pool ───────────
    moderate_html = "<html><body>We feature blackjack, free spins, and teen patti entertainment.</body></html>"

    async def mock_fetch_dispatcher(url, domain, **kwargs):
        dtype = next((item["type"] for item in domain_dataset if item["domain"] == domain), "unknown")
        if dtype == "keyword_gambling":
            return FetchResult(url=url, status_code=200, html=sample_html_gambling)
        elif dtype in ("ai_gambling", "ai_regular"):
            return FetchResult(url=url, status_code=200, html=moderate_html)
        elif dtype == "direct_regular":
            return FetchResult(url=url, status_code=200, html=sample_html_regular)
        elif dtype == "blocked":
            return FetchResult(url=url, status_code=403, failure_type="blocked", error="403 Forbidden")
        else:  # dead
            return FetchResult(url=url, status_code=404, failure_type="dead_confirmed", error="404 Not Found")

    async def mock_ai_dispatcher(html, url="", **kwargs):
        if "ai-gambling" in url:
            return {"verdict": "gambling", "confidence": 0.92, "reason": "AI Confirmed real money games"}
        return {"verdict": "regular", "confidence": 0.88, "reason": "AI Confirmed regular entertainment"}

    async def mock_capture_dispatcher(self, url, output_dir, *args, **kwargs):
        cand_name = _url_to_filename(url)
        target = Path(output_dir) / cand_name
        shutil.copy2(valid_screenshot_path, target)
        return (str(target), "success", None)

    with patch.object(check_runner, "fetch", mock_fetch_dispatcher), \
         patch.object(check_runner, "classify_with_challenge", mock_ai_dispatcher), \
         patch.object(check_runner, "close_ai_session", AsyncMock()), \
         patch.object(check_runner.BrowserPool, "start", AsyncMock()), \
         patch.object(check_runner.BrowserPool, "close", AsyncMock()), \
         patch.object(check_runner.BrowserPool, "capture_url", mock_capture_dispatcher):

        stats = await check_runner.run(concurrency=4, limit=0, mode="new")

    # ── 3. Assert Stage 2 Check Outcomes ──────────────────────────────────────
    assert stats["gambling"] == 8
    assert stats["regular"] == 6
    assert stats["blocked"] == 3
    assert stats["dead"] == 3
    assert stats["screenshots_taken"] == 8

    assert chk_col.count_documents({"status": "gambling"}) == 8
    assert chk_col.count_documents({"status": "regular"}) == 6
    assert chk_col.count_documents({"status": "blocked"}) == 3
    assert chk_col.count_documents({"status": "dead"}) == 3
    assert chk_col.count_documents({"status": "gambling", "screenshot_taken": True}) == 8

    # ── 4. Execute Stage 3 Report Export ──────────────────────────────────────
    export_run_dir = out_dir / "20260817_120000"
    export_run_dir.mkdir(parents=True, exist_ok=True)

    verified_ids = await capture_run(concurrency=4, limit=0)
    assert len(verified_ids) == 8

    excel_res = export_capture_workbook(
        domain_ids=verified_ids,
        output_dir=str(export_run_dir),
        batch_size=0,
        single_file=True,
    )
    assert excel_res["captured"] == 8

    docx_res = build_report_from_mongo(
        domain_ids=verified_ids,
        output_dir=str(export_run_dir),
        batch_size=0,
        single_file=True,
        pdf=False,
    )
    assert len(docx_res["docx_paths"]) == 1
    assert os.path.exists(docx_res["docx_paths"][0])

    # Assert exported state in DB
    assert chk_col.count_documents({"exported": True}) == 8
    assert chk_col.count_documents({"status": "gambling", "exported": {"$ne": True}}) == 0

    # ── 5. Verify /api/stats 100% Agreement ───────────────────────────────────
    api_stats_resp = client.get("/api/stats")
    assert api_stats_resp.status_code == 200
    stats_data = api_stats_resp.json()

    # Source metrics
    assert stats_data["source_domains"]["total_listed"] == 20
    assert stats_data["source_domains"]["processed"] == 20
    assert stats_data["source_domains"]["pending"] == 0

    # Classification metrics
    assert stats_data["checked_domains"]["total_checked"] == 20
    assert stats_data["checked_domains"]["gambling"] == 8
    assert stats_data["checked_domains"]["regular"] == 6
    assert stats_data["checked_domains"]["blocked"] == 3
    assert stats_data["checked_domains"]["dead"] == 3
    assert stats_data["checked_domains"]["screenshot_taken"] == 8
    assert stats_data["checked_domains"]["exported"] == 8
    assert stats_data["checked_domains"]["pending_export"] == 0
    assert stats_data["checked_domains"]["gambling_rate"] == 40.0
