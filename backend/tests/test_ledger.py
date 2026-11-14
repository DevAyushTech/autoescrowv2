import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.infrastructure.database import Database
from autoescrow.models.domain import Agreement
from autoescrow.models.ledger import Escrow, LedgerAccount, LedgerEntry, LedgerTransaction
from autoescrow.models.work import WorkSubmission
from autoescrow.services.ledger import (
    _existing_result,
    _legacy_hash_payload,
    fund_escrow,
    provision_demo_credits,
    release_escrow,
)
from tests.domain_helpers import (
    accept_agreement,
    create_agent,
    create_agreement,
    create_job,
    error_code,
    terms_payload,
)
from tests.helpers import API, login, make_wallet


async def _accepted_agreement(client: TestClient) -> tuple[object, object, dict[str, object]]:
    buyer = login(client, make_wallet(801))
    agent_owner = login(client, make_wallet(802))
    agent = create_agent(client, agent_owner, name="Ledger Agent")
    job = create_job(client, buyer, title="Ledger test job")
    agreement = create_agreement(
        client,
        buyer,
        job_id=str(job["id"]),
        agent_id=str(agent["id"]),
        terms=terms_payload(deadline=job["deadline"]),
    )
    first = accept_agreement(client, buyer, agreement_id=str(agreement["id"]), version_number=1)
    second = accept_agreement(
        client, agent_owner, agreement_id=str(agreement["id"]), version_number=1
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    return buyer, agent_owner, agreement


async def test_simulated_escrow_fund_release_and_idempotent_retry(
    client: TestClient, database: Database
) -> None:
    buyer, agent_owner, agreement = await _accepted_agreement(client)
    created = client.post(
        f"{API}/escrows",
        json={"agreement_id": agreement["id"]},
        headers=buyer.headers,
    )
    assert created.status_code == 201, created.text
    escrow = created.json()
    assert escrow["currency"] == "VCR"
    assert escrow["currency_label"] == "SIMULATED VIRTUAL CREDITS — NO REAL VALUE"
    assert escrow["status"] == "created"
    assert escrow["amount"] == "400.00"

    stranger = login(client, make_wallet(803))
    assert client.get(f"{API}/escrows/{escrow['id']}", headers=stranger.headers).status_code == 404

    insufficient = client.post(
        f"{API}/escrows/{escrow['id']}/fund",
        json={"idempotency_key": "fund-key-insufficient-001"},
        headers=buyer.headers,
    )
    assert insufficient.status_code == 409
    assert error_code(insufficient) == "insufficient_virtual_credits"

    async with database.session() as session:
        await provision_demo_credits(
            session,
            actor_id=buyer.user["id"],
            target_user_id=UUID(buyer.user["id"]),
            amount=Decimal("500.00"),
            idempotency_key="admin-seed-credit-0001",
        )

    funded = client.post(
        f"{API}/escrows/{escrow['id']}/fund",
        json={"idempotency_key": "fund-key-valid-000001"},
        headers=buyer.headers,
    )
    assert funded.status_code == 200, funded.text
    assert funded.json()["status"] == "funded"

    retry = client.post(
        f"{API}/escrows/{escrow['id']}/fund",
        json={"idempotency_key": "fund-key-valid-000001"},
        headers=buyer.headers,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "funded"

    buyer_balance = client.get(f"{API}/ledger/me", headers=buyer.headers)
    assert buyer_balance.status_code == 200
    assert buyer_balance.json()["available"] == "100.00"

    submission = client.post(
        f"{API}/escrows/{escrow['id']}/demo-execute",
        headers=agent_owner.headers,
    )
    assert submission.status_code == 201, submission.text
    reviewed = client.post(
        f"{API}/submissions/{submission.json()['id']}/review",
        json={"decision": "approve", "note": "Demo deliverables reviewed and accepted."},
        headers=buyer.headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "approved"

    release = client.post(
        f"{API}/escrows/{escrow['id']}/release",
        json={"idempotency_key": "release-key-valid-0001"},
        headers=agent_owner.headers,
    )
    assert release.status_code == 200, release.text
    assert release.json()["status"] == "released"

    release_retry = client.post(
        f"{API}/escrows/{escrow['id']}/release",
        json={"idempotency_key": "release-key-valid-0001"},
        headers=agent_owner.headers,
    )
    assert release_retry.status_code == 200
    assert release_retry.json()["status"] == "released"

    beneficiary_balance = client.get(f"{API}/ledger/me", headers=agent_owner.headers)
    assert beneficiary_balance.json()["available"] == "400.00"

    async with database.session() as session:
        transactions = int(
            await session.scalar(select(func.count()).select_from(LedgerTransaction)) or 0
        )
        entries = int(await session.scalar(select(func.count()).select_from(LedgerEntry)) or 0)
        escrow_row = await session.scalar(select(Escrow).where(Escrow.id == UUID(escrow["id"])))
        assert transactions == 3
        assert entries == 6
        assert escrow_row is not None and escrow_row.status == "released"


async def test_simulated_escrow_refund_and_invalid_replay(
    client: TestClient, database: Database
) -> None:
    buyer, agent_owner, agreement = await _accepted_agreement(client)
    created = client.post(
        f"{API}/escrows",
        json={"agreement_id": agreement["id"]},
        headers=buyer.headers,
    )
    assert created.status_code == 201, created.text
    escrow = created.json()

    async with database.session() as session:
        await provision_demo_credits(
            session,
            actor_id=buyer.user["id"],
            target_user_id=UUID(buyer.user["id"]),
            amount=Decimal("500.00"),
            idempotency_key="admin-seed-credit-0002",
        )

    funded = client.post(
        f"{API}/escrows/{escrow['id']}/fund",
        json={"idempotency_key": "fund-refund-key-0001"},
        headers=buyer.headers,
    )
    assert funded.status_code == 200, funded.text

    forbidden = client.post(
        f"{API}/escrows/{escrow['id']}/refund",
        json={"idempotency_key": "refund-key-forbidden-01"},
        headers=agent_owner.headers,
    )
    assert forbidden.status_code == 403
    assert error_code(forbidden) == "buyer_only"

    refunded = client.post(
        f"{API}/escrows/{escrow['id']}/refund",
        json={"idempotency_key": "refund-key-valid-0001"},
        headers=buyer.headers,
    )
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "refunded"

    retry = client.post(
        f"{API}/escrows/{escrow['id']}/refund",
        json={"idempotency_key": "refund-key-valid-0001"},
        headers=buyer.headers,
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "refunded"

    conflict = client.post(
        f"{API}/escrows/{escrow['id']}/refund",
        json={"idempotency_key": "fund-refund-key-0001"},
        headers=buyer.headers,
    )
    assert conflict.status_code == 409
    assert error_code(conflict) == "idempotency_conflict"

    balance = client.get(f"{API}/ledger/me", headers=buyer.headers)
    assert balance.json()["available"] == "500.00"
    assert balance.json()["escrow_locked"] == "0.00"


def test_ledger_requires_authentication(client: TestClient) -> None:
    assert client.get(f"{API}/ledger/me").status_code == 401
    assert client.get(f"{API}/escrows").status_code == 401


def test_demo_credit_setting_is_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="demo_credit_enabled must be false"):
        Settings(
            environment="production",
            demo_credit_enabled=True,
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            redis_url="redis://localhost:6379/0",
            jwt_secret="test-only-jwt-secret-with-more-than-32-bytes",
            siwe_domain="example.com",
            siwe_uri="https://example.com",
        )


async def test_posted_ledger_rows_are_immutable(client: TestClient, database: Database) -> None:
    buyer, _, agreement = await _accepted_agreement(client)
    created = client.post(
        f"{API}/escrows",
        json={"agreement_id": agreement["id"]},
        headers=buyer.headers,
    )
    assert created.status_code == 201, created.text
    async with database.session() as session:
        await provision_demo_credits(
            session,
            actor_id=buyer.user["id"],
            target_user_id=UUID(buyer.user["id"]),
            amount=Decimal("500.00"),
            idempotency_key="admin-seed-credit-0003",
        )
    funded = client.post(
        f"{API}/escrows/{created.json()['id']}/fund",
        json={"idempotency_key": "fund-immutability-001"},
        headers=buyer.headers,
    )
    assert funded.status_code == 200, funded.text

    async with database.session() as session:
        entry = await session.scalar(select(LedgerEntry).limit(1))
        assert entry is not None
        with pytest.raises(DBAPIError):
            entry.amount = Decimal("1.00")
            await session.flush()
        await session.rollback()


async def test_database_rejects_unbalanced_journal_at_commit(
    client: TestClient, database: Database
) -> None:
    buyer = login(client, make_wallet(804))
    async with database.session() as session:
        account = LedgerAccount(
            owner_user_id=UUID(buyer.user["id"]),
            account_type="available",
            currency="VCR",
            balance=Decimal("10.00"),
        )
        session.add(account)
        await session.flush()
        transaction = LedgerTransaction(
            idempotency_key="unbalanced-journal-test-001",
            request_hash="a" * 64,
            kind="demo_credit",
            amount=Decimal("1.00"),
            currency="VCR",
            actor_user_id=UUID(buyer.user["id"]),
            description="Test deliberately unbalanced journal",
        )
        session.add(transaction)
        await session.flush()
        session.add(
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=account.id,
                side="debit",
                amount=Decimal("1.00"),
                currency="VCR",
            )
        )
        await session.flush()
        with pytest.raises(DBAPIError):
            await session.commit()
        await session.rollback()


async def test_demo_credit_idempotency_is_bound_to_actor_and_target(
    client: TestClient, database: Database
) -> None:
    actor = login(client, make_wallet(805))
    other_actor = login(client, make_wallet(806))
    other_target = login(client, make_wallet(807))
    key = "demo-credit-scope-0001"

    async with database.session() as session:
        await provision_demo_credits(
            session,
            actor_id=UUID(actor.user["id"]),
            target_user_id=UUID(actor.user["id"]),
            amount=Decimal("25.00"),
            idempotency_key=key,
        )

    with pytest.raises(ApiError) as target_conflict:
        async with database.session() as session:
            await provision_demo_credits(
                session,
                actor_id=UUID(actor.user["id"]),
                target_user_id=UUID(other_target.user["id"]),
                amount=Decimal("25.00"),
                idempotency_key=key,
            )
    assert target_conflict.value.code == "idempotency_conflict"

    with pytest.raises(ApiError) as actor_conflict:
        async with database.session() as session:
            await provision_demo_credits(
                session,
                actor_id=UUID(other_actor.user["id"]),
                target_user_id=UUID(actor.user["id"]),
                amount=Decimal("25.00"),
                idempotency_key=key,
            )
    assert actor_conflict.value.code == "idempotency_conflict"

    balance = client.get(f"{API}/ledger/me", headers=actor.headers)
    assert balance.status_code == 200
    assert balance.json()["available"] == "25.00"


async def test_concurrent_funding_and_release_cannot_double_settle(
    client: TestClient, database: Database
) -> None:
    buyer, agent_owner, agreement = await _accepted_agreement(client)
    created = client.post(
        f"{API}/escrows",
        json={"agreement_id": agreement["id"]},
        headers=buyer.headers,
    )
    assert created.status_code == 201, created.text
    escrow_id = UUID(created.json()["id"])
    buyer_id = UUID(buyer.user["id"])
    agent_owner_id = UUID(agent_owner.user["id"])

    async with database.session() as session:
        await provision_demo_credits(
            session,
            actor_id=buyer_id,
            target_user_id=buyer_id,
            amount=Decimal("500.00"),
            idempotency_key="concurrent-demo-credit-001",
        )

    async def fund(key: str) -> Escrow:
        async with database.session() as session:
            return await fund_escrow(
                session,
                actor_id=buyer_id,
                escrow_id=escrow_id,
                idempotency_key=key,
            )

    funding_results = await asyncio.gather(
        fund("concurrent-fund-first-001"),
        fund("concurrent-fund-second-001"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Escrow) for result in funding_results) == 1
    funding_errors = [result for result in funding_results if isinstance(result, ApiError)]
    assert len(funding_errors) == 1
    assert funding_errors[0].code == "invalid_escrow_state"

    async with database.session() as session:
        job_id = await session.scalar(
            select(Agreement.job_id).where(Agreement.id == UUID(agreement["id"]))
        )
        assert job_id is not None
        session.add(
            WorkSubmission(
                escrow_id=escrow_id,
                job_id=job_id,
                submitted_by_user_id=agent_owner_id,
                attempt=1,
                status="approved",
                summary="Concurrency test approved work",
                deliverables=[{"name": "test", "status": "accepted"}],
                executor="manual",
            )
        )
        await session.commit()

    async def release(key: str) -> Escrow:
        async with database.session() as session:
            return await release_escrow(
                session,
                actor_id=agent_owner_id,
                escrow_id=escrow_id,
                idempotency_key=key,
            )

    release_results = await asyncio.gather(
        release("concurrent-release-first-001"),
        release("concurrent-release-second-001"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Escrow) for result in release_results) == 1
    release_errors = [result for result in release_results if isinstance(result, ApiError)]
    assert len(release_errors) == 1
    assert release_errors[0].code == "invalid_escrow_state"

    buyer_balance = client.get(f"{API}/ledger/me", headers=buyer.headers)
    agent_balance = client.get(f"{API}/ledger/me", headers=agent_owner.headers)
    assert buyer_balance.json()["available"] == "100.00"
    assert agent_balance.json()["available"] == "400.00"

    async with database.session() as session:
        transaction_count = int(
            await session.scalar(select(func.count()).select_from(LedgerTransaction)) or 0
        )
        entry_count = int(await session.scalar(select(func.count()).select_from(LedgerEntry)) or 0)
        escrow = await session.get(Escrow, escrow_id)
        assert transaction_count == 3
        assert entry_count == 6
        assert escrow is not None and escrow.status == "released"


async def test_legacy_escrow_idempotency_retry_remains_compatible(
    client: TestClient, database: Database
) -> None:
    buyer, agent_owner, agreement = await _accepted_agreement(client)
    stranger = login(client, make_wallet(808))
    created = client.post(
        f"{API}/escrows",
        json={"agreement_id": agreement["id"]},
        headers=buyer.headers,
    )
    assert created.status_code == 201, created.text
    escrow_id = UUID(created.json()["id"])
    buyer_id = UUID(buyer.user["id"])
    key = "legacy-escrow-retry-0001"

    async with database.session() as session:
        transaction = LedgerTransaction(
            idempotency_key=key,
            request_hash=_legacy_hash_payload("escrow_fund", escrow_id, Decimal("400.00")),
            kind="escrow_fund",
            amount=Decimal("400.00"),
            currency="VCR",
            actor_user_id=buyer_id,
            escrow_id=escrow_id,
            description="Legacy posted escrow funding",
        )
        session.add(transaction)
        await session.commit()

    async with database.session() as session:
        escrow = await session.get(Escrow, escrow_id)
        assert escrow is not None
        existing = await _existing_result(
            session,
            kind="escrow_fund",
            escrow=escrow,
            actor_id=buyer_id,
            idempotency_key=key,
        )
        assert existing is not None
        assert existing.id == transaction.id

    async with database.session() as session:
        escrow = await session.get(Escrow, escrow_id)
        assert escrow is not None
        with pytest.raises(ApiError) as conflict:
            await _existing_result(
                session,
                kind="escrow_fund",
                escrow=escrow,
                actor_id=UUID(stranger.user["id"]),
                idempotency_key=key,
            )
    assert conflict.value.code == "idempotency_conflict"
