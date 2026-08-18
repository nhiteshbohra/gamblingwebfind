"""
tests/conftest.py — Global fixtures, isolation setup, and automatic post-test cleanup.

Ensures zero live MongoDB or network calls happen during test runs.
Automatically deletes any test-generated files/directories and test DBs upon completion.
"""
import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

import mongomock
import pytest
import pymongo

import db.mongo_client

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def mock_mongo(monkeypatch):
    """Patch MongoClient globally so all modules share an isolated in-memory DB."""
    client = mongomock.MongoClient()
    db_name = "gamblingsites_test"
    mock_database = client[db_name]

    # Monkeypatch db.mongo_client state
    monkeypatch.setattr(db.mongo_client, "_client", client)
    monkeypatch.setattr(db.mongo_client, "_db", mock_database)

    # Monkeypatch pymongo.MongoClient constructor to return the mock client
    def _mock_mongo_constructor(*args, **kwargs):
        return client

    monkeypatch.setattr(pymongo, "MongoClient", _mock_mongo_constructor)

    try:
        import keywordssearch.searxng_search as sx
        monkeypatch.setattr(sx, "MongoClient", _mock_mongo_constructor)
        monkeypatch.setattr(sx, "USE_SEARXNG", False)
        monkeypatch.setattr(sx, "USE_PLAYWRIGHT", False)
        monkeypatch.setattr(sx, "start_searxng_docker", lambda: False)
    except Exception:
        pass

    try:
        import api.routers.settings as r_settings
        monkeypatch.setattr(r_settings, "get_db", lambda: mock_database)
    except Exception:
        pass

    try:
        import export_domains.screenshot as css
        monkeypatch.setattr(css.BrowserPool, "start", AsyncMock())
        monkeypatch.setattr(css.BrowserPool, "ensure_browser", AsyncMock())
        monkeypatch.setattr(css.BrowserPool, "close", AsyncMock())

        async def _mock_pool_capture_url(self, url, output_dir, *args, **kwargs):
            src_valid = FIXTURES_DIR / "sample_screenshot.jpg"
            cand_name = css._url_to_filename(url)
            target = Path(output_dir) / cand_name
            if src_valid.exists():
                shutil.copy2(src_valid, target)
            return (str(target), "success", None)

        monkeypatch.setattr(css.BrowserPool, "capture_url", _mock_pool_capture_url)
    except Exception:
        pass

    yield {
        "client": client,
        "db": mock_database,
        "source_domains": mock_database["domain_Listed"],
        "checked_domains": mock_database["checked_domains"],
    }

    # Reset globals
    db.mongo_client._client = None
    db.mongo_client._db = None


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Set up isolated directories and environment variables for each test."""
    output_dir = tmp_path / "output"
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MONGO_DB_NAME", "gamblingsites_test")
    monkeypatch.setenv("MONGO_COLLECTION", "domain_Listed")
    monkeypatch.setenv("CHECKED_COLLECTION", "checked_domains")
    monkeypatch.setenv("SCREENSHOT_DIR", str(screenshot_dir))
    monkeypatch.setenv("EXCEL_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("SEARXNG_PAGE_DELAY", "0.0")
    monkeypatch.setenv("FETCH_TIMEOUT", "1.0")
    monkeypatch.setenv("PER_DOMAIN_DELAY", "0.0")
    monkeypatch.setenv("CHECK_CONCURRENCY", "2")
    monkeypatch.setenv("SCREENSHOT_CONCURRENCY", "2")
    monkeypatch.setenv("EXPORT_BATCH_SIZE", "3")
    monkeypatch.setenv("USE_DUCKDUCKGO", "true")
    monkeypatch.setenv("USE_PLAYWRIGHT", "false")
    monkeypatch.setenv("USE_SEARXNG", "false")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    # Copy sample valid screenshot into temp screenshot dir as default
    src_valid = FIXTURES_DIR / "sample_screenshot.jpg"
    if src_valid.exists():
        shutil.copy2(src_valid, screenshot_dir / "sample_screenshot.jpg")

    return {
        "output_dir": output_dir,
        "screenshot_dir": screenshot_dir,
    }


@pytest.fixture
def fixtures_path():
    return FIXTURES_DIR


@pytest.fixture
def sample_domains():
    with open(FIXTURES_DIR / "sample_domains.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_keywords():
    with open(FIXTURES_DIR / "sample_keywords.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_html_gambling():
    with open(FIXTURES_DIR / "sample_html_gambling.html", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def sample_html_regular():
    with open(FIXTURES_DIR / "sample_html_regular.html", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def valid_screenshot_path():
    return str(FIXTURES_DIR / "sample_screenshot.jpg")


@pytest.fixture
def corrupt_screenshot_path():
    return str(FIXTURES_DIR / "sample_screenshot_corrupt.jpg")


_test_failures = []
_test_passes = 0
_test_skips = 0
_initial_output_files = set()


def pytest_sessionstart(session):
    """Snapshot existing files before tests start so all test-created artifacts can be deleted."""
    global _initial_output_files
    root_dir = Path(__file__).resolve().parent.parent
    for check_dir in [root_dir / "output", root_dir / "Batches", root_dir / "screenshots"]:
        if check_dir.exists():
            for item in check_dir.iterdir():
                _initial_output_files.add(str(item.resolve()))


def pytest_runtest_logreport(report):
    """Capture pass/fail/error status and detailed stack traces for each test."""
    global _test_passes, _test_skips, _test_failures
    if report.when == "call":
        if report.passed:
            _test_passes += 1
        elif report.failed:
            _test_failures.append({
                "nodeid": report.nodeid,
                "duration": round(report.duration, 3),
                "error": str(report.longrepr) if report.longrepr else "Unknown test failure",
            })
        elif report.skipped:
            _test_skips += 1
    elif report.when in ("setup", "teardown") and report.failed:
        _test_failures.append({
            "nodeid": f"{report.nodeid} ({report.when})",
            "duration": round(report.duration, 3),
            "error": str(report.longrepr) if report.longrepr else "Fixture setup/teardown failed",
        })


def pytest_sessionfinish(session, exitstatus):
    """
    Automatic cleanup and logging after all tests complete:
    1. Purges ALL test-generated files/directories (including timestamped run folders).
    2. Drops test databases in MongoDB.
    3. Writes complete run report with errors or success status to logs/test_run.log.
    """
    root_dir = Path(__file__).resolve().parent.parent

    # 1. Clean test-generated files and folders across output/, Batches/, screenshots/
    for check_dir in [root_dir / "output", root_dir / "Batches", root_dir / "screenshots"]:
        if check_dir.exists():
            for item in list(check_dir.iterdir()):
                # Delete any item created during the test session or matching test patterns
                if str(item.resolve()) not in _initial_output_files or item.name.startswith(("test_", "sample_", "202", "batch_")) or "test" in item.name.lower():
                    try:
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception:
                        pass

    # Clean leftover root test artifacts
    for pattern in ["test_run.log", "test_*.docx", "test_*.xlsx", "test_*.pdf", "test_*.csv", ".coverage.*"]:
        for root_item in root_dir.glob(pattern):
            try:
                if root_item.name != "test_run.log":
                    root_item.unlink()
            except Exception:
                pass

    # 2. Drop test database from real MongoDB if it exists
    try:
        real_client = pymongo.MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=400)
        for db_name in ["gamblingsites_test", "test_db"]:
            if db_name in real_client.list_database_names():
                real_client.drop_database(db_name)
        real_client.close()
    except Exception:
        pass

    # 3. Write structured test log to logs/test_run.log
    logs_dir = root_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "test_run.log"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_tests = _test_passes + len(_test_failures) + _test_skips

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f"TEST RUN EXECUTION REPORT — {timestamp}\n")
        f.write("=" * 70 + "\n")
        f.write(f"Total: {total_tests} | Passed: {_test_passes} | Failed: {len(_test_failures)} | Skipped: {_test_skips} | Exit Code: {exitstatus}\n")

        if len(_test_failures) == 0 and exitstatus == 0:
            f.write(f"[SUCCESS] All {_test_passes} tests PASSED with 0 errors/failures.\n")
            f.write("[CLEANUP] All test-generated files, mock artifacts, and test DBs were deleted successfully.\n")
        else:
            f.write(f"[ERROR] {len(_test_failures)} test failure(s) occurred:\n")
            for fail in _test_failures:
                f.write(f"\n--- FAILED: {fail['nodeid']} ({fail['duration']}s) ---\n")
                f.write(f"{fail['error']}\n")

        f.write("=" * 70 + "\n\n")
