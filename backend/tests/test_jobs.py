from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from autoescrow.infrastructure.database import Database
from autoescrow.models.domain import Job
from autoescrow.models.enums import JobStatus
from autoescrow.services.audit import AuditEventType
from tests.domain_helpers import create_job, error_code, future_deadline, job_payload
from tests.helpers import API, audit_events, login, make_wallet


def _past_deadline() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def test_job_endpoints_require_authentication(client: TestClient) -> None:
    assert client.post(f"{API}/jobs", json=job_payload()).status_code == 401
    assert client.get(f"{API}/jobs").status_code == 401
    assert client.get(f"{API}/jobs/public").status_code == 200


async def test_create_job_defaults_and_audit(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(301))
    payload = {
        key: value for key, value in job_payload().items() if key not in {"status", "visibility"}
    }

    response = client.post(f"{API}/jobs", json=payload, headers=owner.headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["visibility"] == "private"
    assert body["title"] == payload["title"]
    assert body["budget_amount"] == "500.00"
    assert body["required_skills"] == ["technical-writing"]
    assert body["expected_deliverables"] == ["draft article", "final article"]
    assert body["owner_display_name"] == owner.user["display_name"]
    assert "owner_user_id" not in body
    assert response.headers["Cache-Control"] == "no-store"

    events = await audit_events(database, AuditEventType.JOB_CREATED.value)
    assert len(events) == 1
    assert str(events[0]["subject_id"]) == body["id"]
    assert str(events[0]["actor_user_id"]) == owner.user["id"]
    assert events[0]["metadata"]["visibility"] == "private"


def test_job_create_validation(client: TestClient) -> None:
    owner = login(client, make_wallet(302))

    for invalid_payload in (
        job_payload(deadline=_past_deadline()),
        job_payload(deadline="2030-01-01T00:00:00"),
        job_payload(status="in_progress"),
        job_payload(status="completed"),
        job_payload(budget_amount="0"),
        job_payload(currency="US"),
        job_payload(required_skills=[" "]),
        job_payload(required_capabilities=[]),
        job_payload(expected_deliverables=[]),
        job_payload(title="A"),
        job_payload(visibility="hidden"),
        {**job_payload(), "owner_user_id": "00000000-0000-4000-8000-000000000000"},
    ):
        response = client.post(f"{API}/jobs", json=invalid_payload, headers=owner.headers)
        assert response.status_code == 422, response.text


def test_job_owner_isolation_and_idor(client: TestClient) -> None:
    owner = login(client, make_wallet(303))
    stranger = login(client, make_wallet(304))
    job = create_job(client, owner, status="draft", visibility="private")

    assert client.get(f"{API}/jobs/{job['id']}", headers=stranger.headers).status_code == 404
    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"title": "Hijacked job"},
            headers=stranger.headers,
        ).status_code
        == 404
    )
    assert (
        client.post(f"{API}/jobs/{job['id']}/cancel", headers=stranger.headers).status_code == 404
    )
    listing = client.get(f"{API}/jobs", headers=stranger.headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == []


def test_job_update_and_lifecycle_transitions(client: TestClient) -> None:
    owner = login(client, make_wallet(305))
    job = create_job(client, owner, status="draft", visibility="private")

    published = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"status": "open", "visibility": "public", "title": "Published job"},
        headers=owner.headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "open"
    assert published.json()["visibility"] == "public"
    assert published.json()["title"] == "Published job"

    reverted = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"status": "draft"},
        headers=owner.headers,
    )
    assert reverted.status_code == 200
    assert reverted.json()["status"] == "draft"

    invalid_progress = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"status": "in_progress"},
        headers=owner.headers,
    )
    assert invalid_progress.status_code == 409
    assert error_code(invalid_progress) == "invalid_job_transition"

    invalid_completion = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"status": "completed"},
        headers=owner.headers,
    )
    assert invalid_completion.status_code == 409

    invalid_cancel = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"status": "cancelled"},
        headers=owner.headers,
    )
    assert invalid_cancel.status_code == 409
    assert "cancel endpoint" in invalid_cancel.json()["error"]["message"]

    past_deadline = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"deadline": _past_deadline()},
        headers=owner.headers,
    )
    assert past_deadline.status_code == 422
    assert error_code(past_deadline) == "invalid_deadline"


def test_job_update_rejects_null_empty_and_unknown_fields(client: TestClient) -> None:
    owner = login(client, make_wallet(306))
    job = create_job(client, owner)

    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"title": None},
            headers=owner.headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(f"{API}/jobs/{job['id']}", json={}, headers=owner.headers).status_code == 422
    )
    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"owner_user_id": job["id"]},
            headers=owner.headers,
        ).status_code
        == 422
    )


def test_job_cancellation_is_owner_only_and_idempotent(client: TestClient) -> None:
    owner = login(client, make_wallet(307))
    draft = create_job(client, owner, status="draft", visibility="private")

    cancelled = client.post(f"{API}/jobs/{draft['id']}/cancel", headers=owner.headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    repeated = client.post(f"{API}/jobs/{draft['id']}/cancel", headers=owner.headers)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"

    update_after_cancel = client.patch(
        f"{API}/jobs/{draft['id']}",
        json={"title": "Too late"},
        headers=owner.headers,
    )
    assert update_after_cancel.status_code == 409
    assert error_code(update_after_cancel) == "job_not_editable"


async def test_completed_jobs_cannot_be_edited_or_cancelled(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(308))
    job = create_job(client, owner)

    async with database.session() as session:
        stored = await session.scalar(select(Job).where(Job.id == job["id"]))
        assert stored is not None
        stored.status = JobStatus.COMPLETED.value
        await session.commit()

    update = client.patch(
        f"{API}/jobs/{job['id']}",
        json={"title": "Completed jobs are immutable"},
        headers=owner.headers,
    )
    assert update.status_code == 409
    assert error_code(update) == "job_not_editable"

    cancel = client.post(f"{API}/jobs/{job['id']}/cancel", headers=owner.headers)
    assert cancel.status_code == 409
    assert error_code(cancel) == "job_not_cancellable"


def test_public_job_visibility_rules(client: TestClient) -> None:
    owner = login(client, make_wallet(309))
    stranger = login(client, make_wallet(310))

    public = create_job(client, owner, title="Public open job", status="open", visibility="public")
    private = create_job(
        client, owner, title="Private open job", status="open", visibility="private"
    )
    draft = create_job(client, owner, title="Public draft job", status="draft", visibility="public")
    cancelled = create_job(
        client, owner, title="Cancelled public job", status="open", visibility="public"
    )
    assert (
        client.post(f"{API}/jobs/{cancelled['id']}/cancel", headers=owner.headers).status_code
        == 200
    )

    listing = client.get(f"{API}/jobs/public")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert [item["id"] for item in listing.json()["items"]] == [public["id"]]

    assert client.get(f"{API}/jobs/{public['id']}").status_code == 200
    assert client.get(f"{API}/jobs/{private['id']}").status_code == 404
    assert client.get(f"{API}/jobs/{draft['id']}").status_code == 404
    assert client.get(f"{API}/jobs/{cancelled['id']}").status_code == 404

    # The owner retains access to private and draft jobs.
    assert client.get(f"{API}/jobs/{private['id']}", headers=owner.headers).status_code == 200
    assert client.get(f"{API}/jobs/{draft['id']}", headers=owner.headers).status_code == 200
    assert client.get(f"{API}/jobs/{draft['id']}", headers=stranger.headers).status_code == 404


def test_public_job_search_filters_sorting_and_pagination(client: TestClient) -> None:
    owner = login(client, make_wallet(311))
    create_job(
        client,
        owner,
        title="Write API documentation",
        category="writing",
        deadline=future_deadline(days=5),
        budget_amount="300.00",
    )
    create_job(
        client,
        owner,
        title="Design a landing page",
        category="design",
        deadline=future_deadline(days=3),
        budget_amount="500.00",
        required_skills=["figma"],
        required_capabilities=["prototyping"],
    )
    create_job(
        client,
        owner,
        title="Research market trends",
        category="research",
        deadline=future_deadline(days=9),
        budget_amount="100.00",
        required_skills=["market-research"],
        required_capabilities=["analysis"],
    )
    create_job(
        client,
        owner,
        title="Private writing job",
        category="writing",
        visibility="private",
    )

    assert client.get(f"{API}/jobs/public").json()["total"] == 3
    assert client.get(f"{API}/jobs/public", params={"category": "writing"}).json()["total"] == 1
    assert client.get(f"{API}/jobs/public", params={"search": "API DOC"}).json()["total"] == 1
    assert client.get(f"{API}/jobs/public", params={"search": "missing"}).json()["total"] == 0

    by_deadline = client.get(
        f"{API}/jobs/public",
        params={"sort": "deadline", "order": "asc"},
    )
    assert [item["title"] for item in by_deadline.json()["items"]] == [
        "Design a landing page",
        "Write API documentation",
        "Research market trends",
    ]

    by_budget = client.get(
        f"{API}/jobs/public",
        params={"sort": "budget_amount", "order": "desc"},
    )
    assert [item["title"] for item in by_budget.json()["items"]] == [
        "Design a landing page",
        "Write API documentation",
        "Research market trends",
    ]

    page = client.get(
        f"{API}/jobs/public",
        params={"sort": "budget_amount", "order": "desc", "limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert [item["title"] for item in page.json()["items"]] == ["Write API documentation"]


def test_owned_job_listing_filters_and_pagination(client: TestClient) -> None:
    owner = login(client, make_wallet(312))
    other = login(client, make_wallet(313))
    create_job(client, owner, title="Draft one", status="draft", visibility="private")
    create_job(client, owner, title="Open two", status="open", visibility="public")
    create_job(client, owner, title="Private three", status="open", visibility="private")
    create_job(client, other, title="Other job")

    all_jobs = client.get(f"{API}/jobs", headers=owner.headers)
    assert all_jobs.status_code == 200
    assert all_jobs.json()["total"] == 3

    drafts = client.get(f"{API}/jobs", params={"status": "draft"}, headers=owner.headers)
    assert drafts.status_code == 200
    assert [item["title"] for item in drafts.json()["items"]] == ["Draft one"]

    public_jobs = client.get(
        f"{API}/jobs",
        params={"visibility": "public"},
        headers=owner.headers,
    )
    assert public_jobs.status_code == 200
    assert [item["title"] for item in public_jobs.json()["items"]] == ["Open two"]

    page = client.get(
        f"{API}/jobs",
        params={"limit": 2, "offset": 1},
        headers=owner.headers,
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2


async def test_job_audit_events(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(314))
    job = create_job(client, owner, status="draft", visibility="private")

    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"status": "open", "visibility": "public"},
            headers=owner.headers,
        ).status_code
        == 200
    )
    assert client.post(f"{API}/jobs/{job['id']}/cancel", headers=owner.headers).status_code == 200

    created_events = await audit_events(database, AuditEventType.JOB_CREATED.value)
    updated_events = await audit_events(database, AuditEventType.JOB_UPDATED.value)
    cancelled_events = await audit_events(database, AuditEventType.JOB_CANCELLED.value)

    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert len(cancelled_events) == 1
    for event in [*created_events, *updated_events, *cancelled_events]:
        assert str(event["actor_user_id"]) == owner.user["id"]
        assert str(event["subject_id"]) == job["id"]


def test_public_job_shape_does_not_expose_private_owner_data(client: TestClient) -> None:
    owner = login(client, make_wallet(315))
    job = create_job(client, owner)

    body: dict[str, Any] = client.get(f"{API}/jobs/{job['id']}").json()
    assert "owner_user_id" not in body
    assert "owner_email" not in body
    assert "owner_wallet" not in body
    assert body["owner_display_name"] == owner.user["display_name"]
