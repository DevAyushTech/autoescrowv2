"""Deterministic builders and API helpers for the M2 domain test suite."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import API, LoginSession


def future_deadline(*, days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def agent_payload(
    *,
    name: str = "Atlas Writer",
    status: str = "active",
    is_public: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "description": "A deterministic agent used by the M2 test suite.",
        "category": "writing",
        "capabilities": ["copywriting", "seo"],
        "skills": ["technical-writing", "editing"],
        "supported_task_types": ["blog-post", "documentation"],
        "pricing_model": "fixed",
        "price_amount": "250.00",
        "currency": "USD",
        "availability": "available",
        "status": status,
        "is_public": is_public,
    }
    payload.update(overrides)
    return payload


def job_payload(
    *,
    title: str = "Write a technical article",
    status: str = "open",
    visibility: str = "public",
    deadline: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "description": "Produce a reviewed technical article for the platform.",
        "category": "writing",
        "required_skills": ["technical-writing"],
        "required_capabilities": ["copywriting"],
        "budget_amount": "500.00",
        "currency": "USD",
        "deadline": deadline or future_deadline(days=14),
        "expected_deliverables": ["draft article", "final article"],
        "status": status,
        "visibility": visibility,
    }
    payload.update(overrides)
    return payload


def terms_payload(
    *,
    deadline: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scope": "Write and revise one technical article.",
        "deliverables": ["draft article", "final article"],
        "budget_amount": "400.00",
        "currency": "USD",
        "deadline": deadline or future_deadline(days=7),
        "responsibilities": ["owner supplies the brief", "agent delivers the article"],
        "acceptance_conditions": ["article meets the brief", "article passes review"],
    }
    payload.update(overrides)
    return payload


def create_agent(
    client: TestClient,
    session: LoginSession,
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        f"{API}/agents",
        json=agent_payload(**overrides),
        headers=session.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def create_job(
    client: TestClient,
    session: LoginSession,
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        f"{API}/jobs",
        json=job_payload(**overrides),
        headers=session.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def create_agreement(
    client: TestClient,
    session: LoginSession,
    *,
    job_id: str,
    agent_id: str,
    terms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        f"{API}/agreements",
        json={
            "job_id": job_id,
            "agent_id": agent_id,
            "terms": terms or terms_payload(),
        },
        headers=session.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def accept_agreement(
    client: TestClient,
    session: LoginSession,
    *,
    agreement_id: str,
    version_number: int,
) -> Any:
    return client.post(
        f"{API}/agreements/{agreement_id}/accept",
        json={"version_number": version_number},
        headers=session.headers,
    )


def error_code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value
