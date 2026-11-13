from fastapi.testclient import TestClient

from autoescrow.core.config import Settings
from autoescrow.main import create_app


def test_health_returns_service_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "AutoEscrow API"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"


def test_root_health_alias_is_available(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_valid_request_id_is_preserved(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-request-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-123"


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "bad id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad id"
    assert len(response.headers["X-Request-ID"]) == 32


def test_readiness_reports_both_dependencies(client: TestClient) -> None:
    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_readiness_returns_503_when_redis_is_unavailable(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={
            "redis_url": "redis://127.0.0.1:1/15",
            "redis_connect_timeout": 0.2,
        }
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "redis": "error"},
    }


def test_unknown_route_uses_stable_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_404",
            "message": "Not Found",
        }
    }
