import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "vendorflow-api"
    assert data["version"] == "0.1.0"


def test_health_endpoint():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "vendorflow-api"


def test_readiness_endpoint(monkeypatch: pytest.MonkeyPatch):
    async def mock_database_check():
        return True

    monkeypatch.setattr(
        "app.api.routes.health.check_database",
        mock_database_check,
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"


def test_readiness_endpoint_failure(monkeypatch: pytest.MonkeyPatch):
    async def mock_database_check():
        raise ConnectionRefusedError(
            "Database connection failed: internal postgres detail"
        )

    monkeypatch.setattr(
        "app.api.routes.health.check_database",
        mock_database_check,
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["database"] == "disconnected"
    assert "internal postgres detail" not in response.text


def test_readiness_endpoint_failure_unhealthy(monkeypatch: pytest.MonkeyPatch):
    async def mock_database_check():
        return False

    monkeypatch.setattr(
        "app.api.routes.health.check_database",
        mock_database_check,
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["database"] == "disconnected"
