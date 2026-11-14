from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoescrow.core.config import Settings
from autoescrow.main import create_app
from tests.helpers import API, CHAIN_ID, make_wallet, request_nonce

ALLOWED_ORIGIN = "http://localhost:3000"


@pytest.fixture
def low_nonce_limit_client(test_settings: Settings) -> Generator[TestClient, None, None]:
    settings = test_settings.model_copy(
        update={
            "rate_limit_nonce_requests": 2,
            "rate_limit_window_seconds": 60,
        }
    )
    app: FastAPI = create_app(settings)
    with TestClient(app) as client:
        yield client


def test_nonce_rate_limit_is_enforced(low_nonce_limit_client: TestClient) -> None:
    wallet = make_wallet(121)

    first = request_nonce(low_nonce_limit_client, wallet)
    second = request_nonce(low_nonce_limit_client, wallet)
    third = request_nonce(low_nonce_limit_client, wallet)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(third.headers["Retry-After"]) >= 1


def test_rate_limiter_fails_closed_when_redis_is_unavailable(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(
        update={
            "redis_url": "redis://127.0.0.1:6399/0",
            "redis_connect_timeout": 0.2,
        }
    )
    app = create_app(settings)
    wallet = make_wallet(122)

    with TestClient(app) as client:
        response = request_nonce(client, wallet)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rate_limit_unavailable"
    assert response.headers["Retry-After"] == "5"


def test_cors_preflight_allows_a_configured_origin(client: TestClient) -> None:
    response = client.options(
        f"{API}/auth/wallet/nonce",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_cors_preflight_rejects_an_unconfigured_origin(client: TestClient) -> None:
    response = client.options(
        f"{API}/auth/wallet/nonce",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 400
    assert "Access-Control-Allow-Origin" not in response.headers


def test_cors_simple_request_gets_an_origin_header(client: TestClient) -> None:
    response = client.get(f"{API}/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN


def test_security_headers_and_request_id_are_present(client: TestClient) -> None:
    response = client.get(f"{API}/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Request-ID"]


def test_unknown_route_uses_the_stable_error_envelope(client: TestClient) -> None:
    response = client.get(f"{API}/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"
    assert "message" in response.json()["error"]


def test_validation_errors_use_the_stable_error_envelope(client: TestClient) -> None:
    response = client.post(f"{API}/auth/wallet/nonce", json={"chain_id": CHAIN_ID})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]
    assert body["error"]["details"][0]["location"]


def test_access_token_is_not_accepted_from_the_query_string(client: TestClient) -> None:
    response = client.get(f"{API}/users/me?access_token=test")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_openapi_exposes_all_m1_contracts(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/v1/auth/wallet/nonce" in schema["paths"]
    assert "/api/v1/auth/wallet/verify" in schema["paths"]
    assert "/api/v1/auth/token/refresh" in schema["paths"]
    assert "/api/v1/auth/logout" in schema["paths"]
    assert "/api/v1/auth/sessions" in schema["paths"]
    assert "/api/v1/users/me" in schema["paths"]
    assert "/api/v1/users/me/wallets" in schema["paths"]
    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"


def test_security_headers_include_frame_and_permissions_policy(client: TestClient) -> None:
    response = client.get(f"{API}/health")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


def test_production_responses_include_transport_and_content_security_headers(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(update={"environment": "production"})
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
