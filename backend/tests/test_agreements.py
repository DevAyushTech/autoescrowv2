import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from autoescrow.core.errors import ApiError
from autoescrow.infrastructure.database import Database
from autoescrow.models.domain import Agreement, AgreementAcceptance, AgreementVersion, Job
from autoescrow.models.enums import AgreementStatus, JobStatus
from autoescrow.schemas.agreements import AgreementTerms
from autoescrow.services.agreements import (
    accept_version,
    canonical_terms,
    create_version,
    hash_terms,
)
from autoescrow.services.audit import AuditEventType
from tests.domain_helpers import (
    accept_agreement,
    create_agent,
    create_agreement,
    create_job,
    error_code,
    future_deadline,
    terms_payload,
)
from tests.helpers import API, audit_events, login, make_wallet


def _terms_for_job(job: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    deadline = overrides.pop("deadline", job["deadline"])
    values: dict[str, Any] = terms_payload(deadline=deadline, **overrides)
    return values


async def _stored_agreement(database: Database, agreement_id: str) -> Agreement:
    async with database.session() as session:
        agreement = await session.scalar(
            select(Agreement).where(Agreement.id == UUID(agreement_id))
        )
        assert agreement is not None
        return agreement


def test_agreement_endpoints_require_authentication(client: TestClient) -> None:
    assert client.get(f"{API}/agreements").status_code == 401
    assert (
        client.post(
            f"{API}/agreements",
            json={
                "job_id": "00000000-0000-4000-8000-000000000000",
                "agent_id": "00000000-0000-4000-8000-000000000000",
                "terms": terms_payload(),
            },
        ).status_code
        == 401
    )


async def test_create_agreement_hashes_canonical_terms_and_audits(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(501))
    agent_owner = login(client, make_wallet(502))
    agent = create_agent(client, agent_owner, name="Agreement Agent")
    job = create_job(client, owner, title="Agreement job")
    terms = terms_payload(
        deliverables=[" final article ", "draft article", "draft article"],
        responsibilities=["owner supplies the brief", "agent delivers the article"],
        acceptance_conditions=[" passes review ", "article meets the brief"],
        currency="usd",
        budget_amount="400.0",
        deadline=job["deadline"],
    )

    response = client.post(
        f"{API}/agreements",
        json={"job_id": job["id"], "agent_id": agent["id"], "terms": terms},
        headers=owner.headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"] == job["id"]
    assert body["agent_id"] == agent["id"]
    assert body["owner_user_id"] == owner.user["id"]
    assert body["agent_owner_user_id"] == agent_owner.user["id"]
    assert body["status"] == "pending_acceptance"
    assert body["current_version_number"] == 1
    assert len(body["versions"]) == 1
    assert body["acceptances"] == []

    version = body["versions"][0]
    assert version["version_number"] == 1
    assert len(version["terms_hash"]) == 64
    int(version["terms_hash"], 16)
    assert version["terms"]["deliverables"] == ["draft article", "final article"]
    assert version["terms"]["responsibilities"] == [
        "agent delivers the article",
        "owner supplies the brief",
    ]
    assert version["terms"]["acceptance_conditions"] == [
        "article meets the brief",
        "passes review",
    ]
    assert version["terms"]["currency"] == "USD"
    assert version["terms"]["budget_amount"] == "400.00"

    validated_terms = AgreementTerms.model_validate(terms)
    assert version["terms_hash"] == hash_terms(canonical_terms(validated_terms))
    assert version["created_by_user_id"] == owner.user["id"]

    owner_read = client.get(f"{API}/agreements/{body['id']}", headers=owner.headers)
    agent_read = client.get(f"{API}/agreements/{body['id']}", headers=agent_owner.headers)
    stranger_read = client.get(
        f"{API}/agreements/{body['id']}",
        headers=login(client, make_wallet(503)).headers,
    )
    assert owner_read.status_code == 200
    assert agent_read.status_code == 200
    assert stranger_read.status_code == 404

    created_events = await audit_events(database, AuditEventType.AGREEMENT_CREATED.value)
    version_events = await audit_events(database, AuditEventType.AGREEMENT_VERSION_CREATED.value)
    assert len(created_events) == 1
    assert len(version_events) == 1
    assert str(created_events[0]["subject_id"]) == body["id"]
    assert str(version_events[0]["subject_id"]) == version["id"]
    assert str(created_events[0]["actor_user_id"]) == owner.user["id"]


def test_agreement_creation_validation_and_authorization(client: TestClient) -> None:
    owner = login(client, make_wallet(504))
    other = login(client, make_wallet(505))
    agent_owner = login(client, make_wallet(506))
    agent = create_agent(client, agent_owner, name="Validation Agent")
    job = create_job(client, owner, title="Validation job")

    not_owner = client.post(
        f"{API}/agreements",
        json={"job_id": job["id"], "agent_id": agent["id"], "terms": _terms_for_job(job)},
        headers=other.headers,
    )
    assert not_owner.status_code == 404
    assert error_code(not_owner) == "job_not_found"

    draft_job = create_job(client, owner, title="Draft job", status="draft", visibility="private")
    draft_response = client.post(
        f"{API}/agreements",
        json={
            "job_id": draft_job["id"],
            "agent_id": agent["id"],
            "terms": _terms_for_job(draft_job),
        },
        headers=owner.headers,
    )
    assert draft_response.status_code == 409
    assert error_code(draft_response) == "job_not_open"

    private_agent = create_agent(client, agent_owner, name="Private Agent", is_public=False)
    private_response = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": private_agent["id"],
            "terms": _terms_for_job(job),
        },
        headers=owner.headers,
    )
    assert private_response.status_code == 404
    assert error_code(private_response) == "agent_not_found"

    self_agent = create_agent(client, owner, name="Self Agent")
    self_response = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": self_agent["id"],
            "terms": _terms_for_job(job),
        },
        headers=owner.headers,
    )
    assert self_response.status_code == 409
    assert error_code(self_response) == "self_agreement"

    invalid_terms = [
        {"currency": "EUR"},
        {"budget_amount": "500.01"},
        {"deadline": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
        {"deadline": future_deadline(days=30)},
        {"deliverables": []},
        {"budget_amount": "0"},
        {"unknown": "field"},
    ]
    for overrides in invalid_terms:
        response = client.post(
            f"{API}/agreements",
            json={
                "job_id": job["id"],
                "agent_id": agent["id"],
                "terms": _terms_for_job(job, **overrides),
            },
            headers=owner.headers,
        )
        assert response.status_code == 422, (overrides, response.text)

    created = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    duplicate = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": agent["id"],
            "terms": _terms_for_job(job),
        },
        headers=owner.headers,
    )
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "agreement_exists"
    assert created["id"]


def test_agreement_versioning_and_stale_version_rejection(client: TestClient) -> None:
    owner = login(client, make_wallet(507))
    agent_owner = login(client, make_wallet(508))
    stranger = login(client, make_wallet(509))
    agent = create_agent(client, agent_owner, name="Versioning Agent")
    job = create_job(client, owner, title="Versioning job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    first_hash = agreement["versions"][0]["terms_hash"]

    stale = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 2,
            "terms": _terms_for_job(job, scope="A newer scope."),
        },
        headers=owner.headers,
    )
    assert stale.status_code == 409
    assert error_code(stale) == "stale_agreement_version"

    second = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 1,
            "terms": _terms_for_job(job, scope="Revised scope with extra review."),
        },
        headers=owner.headers,
    )
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["current_version_number"] == 2
    assert second_body["status"] == "pending_acceptance"
    assert len(second_body["versions"]) == 2
    assert second_body["versions"][0]["terms_hash"] == first_hash
    assert second_body["versions"][1]["terms_hash"] != first_hash
    assert second_body["versions"][1]["terms"]["scope"] == "Revised scope with extra review."
    assert second_body["versions"][1]["created_by_user_id"] == owner.user["id"]

    third = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 2,
            "terms": _terms_for_job(job, scope="Agent-owner revision."),
        },
        headers=agent_owner.headers,
    )
    assert third.status_code == 201, third.text
    assert third.json()["current_version_number"] == 3
    assert third.json()["versions"][2]["created_by_user_id"] == agent_owner.user["id"]

    old_expected = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 1,
            "terms": _terms_for_job(job),
        },
        headers=owner.headers,
    )
    assert old_expected.status_code == 409
    assert error_code(old_expected) == "stale_agreement_version"

    non_participant = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 3,
            "terms": _terms_for_job(job),
        },
        headers=stranger.headers,
    )
    assert non_participant.status_code == 404

    invalid_update = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 3,
            "terms": _terms_for_job(job, currency="EUR"),
        },
        headers=owner.headers,
    )
    assert invalid_update.status_code == 422
    assert error_code(invalid_update) == "currency_mismatch"

    cancelled = client.post(f"{API}/jobs/{job['id']}/cancel", headers=owner.headers)
    assert cancelled.status_code == 200
    closed = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 3,
            "terms": _terms_for_job(job),
        },
        headers=owner.headers,
    )
    assert closed.status_code == 409
    assert error_code(closed) == "job_not_open"


async def test_both_parties_accept_current_version_and_activate(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(510))
    agent_owner = login(client, make_wallet(511))
    stranger = login(client, make_wallet(512))
    agent = create_agent(client, agent_owner, name="Acceptance Agent")
    job = create_job(client, owner, title="Acceptance job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )

    owner_acceptance = accept_agreement(
        client,
        owner,
        agreement_id=agreement["id"],
        version_number=1,
    )
    assert owner_acceptance.status_code == 200, owner_acceptance.text
    assert owner_acceptance.json()["status"] == "pending_acceptance"
    assert len(owner_acceptance.json()["acceptances"]) == 1
    assert owner_acceptance.json()["acceptances"][0]["party"] == "owner"
    assert owner_acceptance.json()["acceptances"][0]["user_id"] == owner.user["id"]
    assert owner_acceptance.json()["acceptances"][0]["accepted_at"]
    assert client.get(f"{API}/jobs/{job['id']}", headers=owner.headers).json()["status"] == "open"

    duplicate = accept_agreement(
        client,
        owner,
        agreement_id=agreement["id"],
        version_number=1,
    )
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "already_accepted"

    agent_acceptance = accept_agreement(
        client,
        agent_owner,
        agreement_id=agreement["id"],
        version_number=1,
    )
    assert agent_acceptance.status_code == 200, agent_acceptance.text
    body = agent_acceptance.json()
    assert body["status"] == "active"
    assert len(body["acceptances"]) == 2
    assert {item["party"] for item in body["acceptances"]} == {"owner", "agent_owner"}
    assert {item["user_id"] for item in body["acceptances"]} == {
        owner.user["id"],
        agent_owner.user["id"],
    }
    assert all(item["accepted_at"] for item in body["acceptances"])
    assert body["current_version_number"] == 1

    job_body = client.get(f"{API}/jobs/{job['id']}", headers=owner.headers).json()
    assert job_body["status"] == "in_progress"

    duplicate_agent = accept_agreement(
        client,
        agent_owner,
        agreement_id=agreement["id"],
        version_number=1,
    )
    assert duplicate_agent.status_code == 409

    non_participant = accept_agreement(
        client,
        stranger,
        agreement_id=agreement["id"],
        version_number=1,
    )
    assert non_participant.status_code == 404

    stored = await _stored_agreement(database, agreement["id"])
    assert stored.status == AgreementStatus.ACTIVE.value
    async with database.session() as session:
        acceptances = int(
            await session.scalar(
                select(func.count())
                .select_from(AgreementAcceptance)
                .where(AgreementAcceptance.agreement_version_id == UUID(body["versions"][0]["id"]))
            )
            or 0
        )
    assert acceptances == 2

    accepted_events = await audit_events(database, AuditEventType.AGREEMENT_ACCEPTED.value)
    activated_events = await audit_events(database, AuditEventType.AGREEMENT_ACTIVATED.value)
    assert len(accepted_events) == 2
    assert len(activated_events) == 1


def test_acceptance_rejects_outdated_versions_and_unavailable_jobs(client: TestClient) -> None:
    owner = login(client, make_wallet(513))
    agent_owner = login(client, make_wallet(514))
    agent = create_agent(client, agent_owner, name="Availability Agent")
    job = create_job(client, owner, title="Availability job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )

    outdated = accept_agreement(
        client,
        owner,
        agreement_id=agreement["id"],
        version_number=4,
    )
    assert outdated.status_code == 409
    assert error_code(outdated) == "outdated_agreement_version"

    updated = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 1,
            "terms": _terms_for_job(job, scope="Second version."),
        },
        headers=owner.headers,
    )
    assert updated.status_code == 201
    assert (
        accept_agreement(
            client,
            owner,
            agreement_id=agreement["id"],
            version_number=1,
        ).status_code
        == 409
    )

    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"status": "draft"},
            headers=owner.headers,
        ).status_code
        == 200
    )
    draft_acceptance = accept_agreement(
        client,
        owner,
        agreement_id=agreement["id"],
        version_number=2,
    )
    assert draft_acceptance.status_code == 409
    assert error_code(draft_acceptance) == "job_not_open"

    assert (
        client.patch(
            f"{API}/jobs/{job['id']}",
            json={"status": "open"},
            headers=owner.headers,
        ).status_code
        == 200
    )
    assert client.post(f"{API}/jobs/{job['id']}/cancel", headers=owner.headers).status_code == 200
    cancelled_acceptance = accept_agreement(
        client,
        owner,
        agreement_id=agreement["id"],
        version_number=2,
    )
    assert cancelled_acceptance.status_code == 409
    assert error_code(cancelled_acceptance) == "job_not_open"


async def test_versioning_after_activation_requires_new_acceptances(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(515))
    agent_owner = login(client, make_wallet(516))
    agent = create_agent(client, agent_owner, name="Revision Agent")
    job = create_job(client, owner, title="Revision job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    assert (
        accept_agreement(
            client,
            owner,
            agreement_id=agreement["id"],
            version_number=1,
        ).status_code
        == 200
    )
    assert (
        accept_agreement(
            client,
            agent_owner,
            agreement_id=agreement["id"],
            version_number=1,
        ).status_code
        == 200
    )

    revised = client.post(
        f"{API}/agreements/{agreement['id']}/versions",
        json={
            "expected_version_number": 1,
            "terms": _terms_for_job(job, scope="Post-activation revision."),
        },
        headers=agent_owner.headers,
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["status"] == "pending_acceptance"
    assert revised.json()["current_version_number"] == 2
    assert len(revised.json()["acceptances"]) == 2

    assert (
        accept_agreement(
            client,
            owner,
            agreement_id=agreement["id"],
            version_number=2,
        ).status_code
        == 200
    )
    final = accept_agreement(
        client,
        agent_owner,
        agreement_id=agreement["id"],
        version_number=2,
    )
    assert final.status_code == 200
    assert final.json()["status"] == "active"
    assert len(final.json()["acceptances"]) == 4
    job_body = client.get(f"{API}/jobs/{job['id']}", headers=owner.headers).json()
    assert job_body["status"] == "in_progress"

    stored = await _stored_agreement(database, agreement["id"])
    assert stored.current_version_number == 2


async def test_concurrent_acceptance_is_serialized(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(517))
    agent_owner = login(client, make_wallet(518))
    agent = create_agent(client, agent_owner, name="Concurrent Agent")
    job = create_job(client, owner, title="Concurrent job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    agreement_id = UUID(agreement["id"])

    async def accept(actor_id: UUID) -> Any:
        async with database.session() as session:
            return await accept_version(
                session,
                actor_id=actor_id,
                agreement_id=agreement_id,
                version_number=1,
            )

    results = await asyncio.gather(
        accept(UUID(owner.user["id"])),
        accept(UUID(agent_owner.user["id"])),
        return_exceptions=True,
    )
    assert all(not isinstance(result, Exception) for result in results), results

    async with database.session() as session:
        stored = await session.scalar(select(Agreement).where(Agreement.id == agreement_id))
        assert stored is not None
        assert stored.status == AgreementStatus.ACTIVE.value
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(AgreementAcceptance)
                .where(
                    AgreementAcceptance.agreement_version_id == UUID(agreement["versions"][0]["id"])
                )
            )
            or 0
        )
        assert count == 2
        job_row = await session.scalar(select(Job).where(Job.id == UUID(job["id"])))
        assert job_row is not None
        assert job_row.status == JobStatus.IN_PROGRESS.value


async def test_concurrent_duplicate_acceptance_rejects_one(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(519))
    agent_owner = login(client, make_wallet(520))
    agent = create_agent(client, agent_owner, name="Duplicate Agent")
    job = create_job(client, owner, title="Duplicate job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    actor_id = UUID(owner.user["id"])

    async def accept() -> Any:
        async with database.session() as session:
            return await accept_version(
                session,
                actor_id=actor_id,
                agreement_id=UUID(agreement["id"]),
                version_number=1,
            )

    results = await asyncio.gather(accept(), accept(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, ApiError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 409
    assert failures[0].code == "already_accepted"


async def test_concurrent_version_creation_rejects_stale_expected_version(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(521))
    agent_owner = login(client, make_wallet(522))
    agent = create_agent(client, agent_owner, name="Race Agent")
    job = create_job(client, owner, title="Race job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    terms = AgreementTerms.model_validate(_terms_for_job(job, scope="Race revision."))

    async def version() -> Any:
        async with database.session() as session:
            return await create_version(
                session,
                actor_id=UUID(owner.user["id"]),
                agreement_id=UUID(agreement["id"]),
                expected_version_number=1,
                terms=terms,
            )

    results = await asyncio.gather(version(), version(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, ApiError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 409
    assert failures[0].code == "stale_agreement_version"

    async with database.session() as session:
        versions = list(
            await session.scalars(
                select(AgreementVersion).where(
                    AgreementVersion.agreement_id == UUID(agreement["id"])
                )
            )
        )
    assert len(versions) == 2


def test_canonical_terms_and_hash_are_order_and_timezone_independent() -> None:
    utc_deadline = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    offset_deadline = datetime(
        2030,
        1,
        1,
        14,
        0,
        tzinfo=timezone(timedelta(hours=2)),
    )
    first = AgreementTerms(
        scope="Canonical terms.",
        deliverables=["b", "a"],
        budget_amount="400.0",
        currency="usd",
        deadline=utc_deadline,
        responsibilities=["two", "one"],
        acceptance_conditions=["passes"],
    )
    second = AgreementTerms(
        scope="Canonical terms.",
        deliverables=["a", "b"],
        budget_amount="400.00",
        currency="USD",
        deadline=offset_deadline,
        responsibilities=["one", "two"],
        acceptance_conditions=["passes"],
    )
    third = AgreementTerms(
        scope="Different scope.",
        deliverables=["a", "b"],
        budget_amount="400.00",
        currency="USD",
        deadline=utc_deadline,
        responsibilities=["one", "two"],
        acceptance_conditions=["passes"],
    )

    assert canonical_terms(first) == canonical_terms(second)
    assert hash_terms(canonical_terms(first)) == hash_terms(canonical_terms(second))
    assert hash_terms(canonical_terms(first)) != hash_terms(canonical_terms(third))
    assert canonical_terms(first)["deadline"] == "2030-01-01T12:00:00Z"
    assert canonical_terms(first)["budget_amount"] == "400.00"


async def test_database_trigger_rejects_agreement_version_mutation(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(523))
    agent_owner = login(client, make_wallet(524))
    agent = create_agent(client, agent_owner, name="Immutable Agent")
    job = create_job(client, owner, title="Immutable job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=_terms_for_job(job),
    )
    version_id = UUID(agreement["versions"][0]["id"])
    original_hash = agreement["versions"][0]["terms_hash"]

    async with database.session() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                update(AgreementVersion)
                .where(AgreementVersion.id == version_id)
                .values(terms={"scope": "Tampered."})
            )
        await session.rollback()

    async with database.session() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(delete(AgreementVersion).where(AgreementVersion.id == version_id))
        await session.rollback()

    async with database.session() as session:
        stored = await session.scalar(
            select(AgreementVersion).where(AgreementVersion.id == version_id)
        )
        assert stored is not None
        assert stored.terms_hash == original_hash
        trigger_count = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'agreement_versions_immutable'"
                )
            )
            or 0
        )
    assert trigger_count == 1


async def test_agreement_listing_is_participant_scoped_and_paginated(
    client: TestClient,
) -> None:
    owner = login(client, make_wallet(525))
    agent_owner = login(client, make_wallet(526))
    stranger = login(client, make_wallet(527))
    agent = create_agent(client, agent_owner, name="Listing Agent")
    jobs = [create_job(client, owner, title=f"Listing job {index}") for index in range(3)]
    created = [
        create_agreement(
            client,
            owner,
            job_id=job["id"],
            agent_id=agent["id"],
            terms=_terms_for_job(job),
        )
        for job in jobs
    ]

    owner_list = client.get(f"{API}/agreements", headers=owner.headers)
    agent_list = client.get(f"{API}/agreements", headers=agent_owner.headers)
    stranger_list = client.get(f"{API}/agreements", headers=stranger.headers)
    assert owner_list.status_code == 200
    assert owner_list.json()["total"] == 3
    assert agent_list.json()["total"] == 3
    assert stranger_list.json()["total"] == 0
    assert set(owner_list.json()["items"][0]) >= {
        "id",
        "job_id",
        "status",
        "current_version_number",
        "versions",
    }

    page = client.get(
        f"{API}/agreements",
        params={"limit": 2, "offset": 2},
        headers=owner.headers,
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["id"] in {item["id"] for item in created}


def test_agreement_terms_reject_empty_and_extra_fields(client: TestClient) -> None:
    owner = login(client, make_wallet(528))
    agent_owner = login(client, make_wallet(529))
    agent = create_agent(client, agent_owner, name="Schema Agent")
    job = create_job(client, owner, title="Schema job")

    empty_lists = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": agent["id"],
            "terms": _terms_for_job(job, responsibilities=[]),
        },
        headers=owner.headers,
    )
    assert empty_lists.status_code == 422

    extra_top_level = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": agent["id"],
            "terms": _terms_for_job(job),
            "accepted_by": owner.user["id"],
        },
        headers=owner.headers,
    )
    assert extra_top_level.status_code == 422

    extra_terms = client.post(
        f"{API}/agreements",
        json={
            "job_id": job["id"],
            "agent_id": agent["id"],
            "terms": _terms_for_job(job, owner_user_id=owner.user["id"]),
        },
        headers=owner.headers,
    )
    assert extra_terms.status_code == 422

    extra_accept = client.post(
        f"{API}/agreements/{job['id']}/accept",
        json={"version_number": 1, "user_id": owner.user["id"]},
        headers=owner.headers,
    )
    assert extra_accept.status_code == 422
