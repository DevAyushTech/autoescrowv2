from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, IntegrityError

from autoescrow.infrastructure.database import Database
from autoescrow.models.domain import Agent, Agreement, AgreementAcceptance, AgreementVersion, Job
from tests.domain_helpers import create_agent, create_agreement, create_job, terms_payload
from tests.helpers import login, make_wallet


def _agent_model(owner_id: UUID, **overrides: Any) -> Agent:
    values: dict[str, Any] = {
        "owner_user_id": owner_id,
        "name": "Constraint Agent",
        "description": "Constraint fixture.",
        "category": "writing",
        "capabilities": ["copywriting"],
        "skills": ["technical-writing"],
        "supported_task_types": ["blog-post"],
        "pricing_model": "fixed",
        "price_amount": Decimal("100.00"),
        "currency": "USD",
        "availability": "available",
        "status": "active",
        "is_public": True,
    }
    values.update(overrides)
    return Agent(**values)


def _job_model(owner_id: UUID, **overrides: Any) -> Job:
    values: dict[str, Any] = {
        "owner_user_id": owner_id,
        "title": "Constraint job",
        "description": "Constraint fixture.",
        "category": "writing",
        "required_skills": ["technical-writing"],
        "required_capabilities": ["copywriting"],
        "budget_amount": Decimal("500.00"),
        "currency": "USD",
        "deadline": datetime.now(UTC) + timedelta(days=7),
        "expected_deliverables": ["final article"],
        "status": "open",
        "visibility": "public",
    }
    values.update(overrides)
    return Job(**values)


async def test_agent_check_and_unique_constraints(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(601))
    owner_id = UUID(owner.user["id"])
    create_agent(client, owner, name="Unique Agent")

    async with database.session() as session:
        invalid_agents = [
            _agent_model(owner_id, price_amount=Decimal("0")),
            _agent_model(owner_id, name="Invalid Status", status="deleted"),
            _agent_model(owner_id, name="Invalid Category", category="unknown"),
            _agent_model(owner_id, name="Invalid Pricing", pricing_model="free"),
            _agent_model(owner_id, name="Invalid Skills", skills=[]),
            _agent_model(owner_id, name="Invalid JSON", skills="not-an-array"),
            _agent_model(owner_id, name="Invalid Currency", currency="usd"),
            _agent_model(owner_id, name="unique agent"),
        ]
        for candidate in invalid_agents:
            session.add(candidate)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()


async def test_job_check_constraints(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(602))
    owner_id = UUID(owner.user["id"])

    async with database.session() as session:
        invalid_jobs = [
            _job_model(owner_id, budget_amount=Decimal("0")),
            _job_model(owner_id, status="deleted"),
            _job_model(owner_id, visibility="hidden"),
            _job_model(owner_id, category="unknown"),
            _job_model(owner_id, required_skills=[]),
            _job_model(owner_id, required_capabilities=[]),
            _job_model(owner_id, expected_deliverables=[]),
            _job_model(owner_id, required_skills="not-an-array"),
        ]
        for candidate in invalid_jobs:
            session.add(candidate)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        # VARCHAR(3) rejects an overlong currency at the driver boundary before
        # PostgreSQL can raise a check-constraint IntegrityError.
        session.add(_job_model(owner_id, currency="USDX"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()


async def test_agreement_check_and_unique_constraints(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(603))
    agent_owner = login(client, make_wallet(604))
    owner_id = UUID(owner.user["id"])
    agent_owner_id = UUID(agent_owner.user["id"])
    agent = create_agent(client, agent_owner, name="Constraint Agent")
    job = create_job(client, owner, title="Constraint agreement job")

    async with database.session() as session:
        self_dealing = Agreement(
            job_id=UUID(job["id"]),
            agent_id=UUID(agent["id"]),
            owner_user_id=agent_owner_id,
            agent_owner_user_id=agent_owner_id,
            created_by_user_id=agent_owner_id,
            status="pending_acceptance",
            current_version_number=1,
        )
        session.add(self_dealing)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with database.session() as session:
        invalid_version = Agreement(
            job_id=UUID(job["id"]),
            agent_id=UUID(agent["id"]),
            owner_user_id=owner_id,
            agent_owner_user_id=agent_owner_id,
            created_by_user_id=owner_id,
            status="deleted",
            current_version_number=1,
        )
        session.add(invalid_version)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=terms_payload(deadline=job["deadline"]),
    )

    async with database.session() as session:
        duplicate = Agreement(
            job_id=UUID(job["id"]),
            agent_id=UUID(agent["id"]),
            owner_user_id=owner_id,
            agent_owner_user_id=agent_owner_id,
            created_by_user_id=owner_id,
            status="pending_acceptance",
            current_version_number=1,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with database.session() as session:
        duplicate_version = AgreementVersion(
            agreement_id=UUID(agreement["id"]),
            version_number=1,
            terms={"scope": "duplicate"},
            terms_hash="0" * 64,
            created_by_user_id=owner_id,
        )
        session.add(duplicate_version)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with database.session() as session:
        invalid_hash = AgreementVersion(
            agreement_id=UUID(agreement["id"]),
            version_number=2,
            terms={"scope": "invalid hash"},
            terms_hash="not-a-hash",
            created_by_user_id=owner_id,
        )
        session.add(invalid_hash)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_acceptance_unique_constraints(client: TestClient, database: Database) -> None:
    owner = login(client, make_wallet(605))
    agent_owner = login(client, make_wallet(606))
    owner_id = UUID(owner.user["id"])
    agent_owner_id = UUID(agent_owner.user["id"])
    agent = create_agent(client, agent_owner, name="Acceptance Constraint Agent")
    job = create_job(client, owner, title="Acceptance constraint job")
    agreement = create_agreement(
        client,
        owner,
        job_id=job["id"],
        agent_id=agent["id"],
        terms=terms_payload(deadline=job["deadline"]),
    )
    version_id = UUID(agreement["versions"][0]["id"])

    async with database.session() as session:
        session.add(
            AgreementAcceptance(
                agreement_version_id=version_id,
                user_id=owner_id,
                party="owner",
            )
        )
        await session.commit()

    async with database.session() as session:
        session.add(
            AgreementAcceptance(
                agreement_version_id=version_id,
                user_id=owner_id,
                party="owner",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with database.session() as session:
        session.add(
            AgreementAcceptance(
                agreement_version_id=version_id,
                user_id=agent_owner_id,
                party="owner",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_domain_tables_keep_ownership_foreign_keys(
    client: TestClient, database: Database
) -> None:
    owner = login(client, make_wallet(607))
    other = login(client, make_wallet(608))
    owner_id = UUID(owner.user["id"])
    other_id = UUID(other.user["id"])

    async with database.session() as session:
        agent = _agent_model(owner_id, name="Foreign Key Agent")
        session.add(agent)
        await session.flush()
        assert agent.owner_user_id == owner_id

        job = _job_model(other_id, title="Foreign Key Job")
        session.add(job)
        await session.flush()
        assert job.owner_user_id == other_id
        await session.rollback()
