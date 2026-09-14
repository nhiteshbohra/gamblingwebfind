from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_api_settings():
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "mongo" in data
    assert "omniroute" in data
    assert "config" in data


def test_api_reports():
    response = client.get("/api/reports")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_stats():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "source_domains" in data
    assert "checked_domains" in data


def test_api_domains_query():
    response = client.get("/api/domains?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total" in data
    assert "page" in data

