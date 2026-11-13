import hashlib
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.models.domain import Agreement, AgreementVersion
from autoescrow.models.enums import AgreementStatus
from autoescrow.models.identity import User
from autoescrow.models.ledger import Escrow, LedgerAccount, LedgerEntry, LedgerTransaction
from autoescrow.services.audit import AuditEventType, record_audit


def _hash_payload(kind: str, escrow_id: UUID | None, amount: Decimal) -> str:
    payload = {
        "kind": kind,
        "escrow_id": str(escrow_id) if escrow_id else None,
        "amount": str(amount),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _account(
    session: AsyncSession, owner_id: UUID | None, kind: str, *, lock: bool = False
) -> LedgerAccount:
    statement = select(LedgerAccount).where(
        LedgerAccount.owner_user_id == owner_id, LedgerAccount.account_type == kind
    )
    if lock:
        statement = statement.with_for_update()
    account = await session.scalar(statement)
    if account is None:
        if owner_id is None:
            raise ApiError(500, "ledger_owner_required", "A user ledger account requires an owner.")
        await session.execute(
            insert(LedgerAccount)
            .values(
                owner_user_id=owner_id,
                account_type=kind,
                currency="VCR",
                balance=Decimal("0.00"),
            )
            .on_conflict_do_nothing(constraint="uq_ledger_account_owner_type")
        )
        account = await session.scalar(statement)
    if account is None:
        raise ApiError(
            500, "ledger_account_unavailable", "A user ledger account could not be created."
        )
    return account


async def _post(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    kind: str,
    amount: Decimal,
    source: LedgerAccount,
    destination: LedgerAccount,
    idempotency_key: str,
    escrow_id: UUID | None,
    description: str,
) -> LedgerTransaction:
    digest = _hash_payload(kind, escrow_id, amount)
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(idempotency_key))))
    existing = await session.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise ApiError(
                409,
                "idempotency_conflict",
                "Idempotency key was already used for a different request.",
            )
        return existing
    locked_source = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.id == source.id).with_for_update()
    )
    locked_destination = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.id == destination.id).with_for_update()
    )
    if locked_source is None or locked_destination is None:
        raise ApiError(500, "ledger_account_missing", "A ledger account is unavailable.")
    if locked_source.balance < amount:
        raise ApiError(
            409, "insufficient_virtual_credits", "Insufficient available virtual credits."
        )
    tx = LedgerTransaction(
        idempotency_key=idempotency_key,
        request_hash=digest,
        kind=kind,
        amount=amount,
        currency="VCR",
        actor_user_id=actor_id,
        escrow_id=escrow_id,
        description=description,
    )
    session.add(tx)
    await session.flush()
    locked_source.balance -= amount
    locked_destination.balance += amount
    session.add_all(
        [
            LedgerEntry(
                transaction_id=tx.id,
                account_id=locked_source.id,
                side="debit",
                amount=amount,
                currency="VCR",
            ),
            LedgerEntry(
                transaction_id=tx.id,
                account_id=locked_destination.id,
                side="credit",
                amount=amount,
                currency="VCR",
            ),
        ]
    )
    await session.flush()
    debit = await session.scalar(
        select(func.sum(LedgerEntry.amount)).where(
            LedgerEntry.transaction_id == tx.id, LedgerEntry.side == "debit"
        )
    )
    credit = await session.scalar(
        select(func.sum(LedgerEntry.amount)).where(
            LedgerEntry.transaction_id == tx.id, LedgerEntry.side == "credit"
        )
    )
    if debit != credit:
        raise ApiError(
            500, "unbalanced_ledger", "Ledger transaction failed its balancing invariant."
        )
    await record_audit(
        session,
        event_type={
            "escrow_fund": AuditEventType.ESCROW_FUNDED,
            "escrow_release": AuditEventType.ESCROW_RELEASED,
            "escrow_refund": AuditEventType.ESCROW_REFUNDED,
            "demo_credit": AuditEventType.LEDGER_DEMO_CREDIT,
        }[kind],
        subject_type="ledger_transaction",
        subject_id=tx.id,
        actor_user_id=actor_id,
        metadata={
            "escrow_id": str(escrow_id) if escrow_id else None,
            "amount": str(amount),
            "currency": "VCR",
        },
    )
    return tx


async def create_escrow(session: AsyncSession, *, actor_id: UUID, agreement_id: UUID) -> Escrow:
    agreement = await session.scalar(
        select(Agreement).where(Agreement.id == agreement_id).with_for_update()
    )
    if agreement is None or actor_id not in {
        agreement.owner_user_id,
        agreement.agent_owner_user_id,
    }:
        raise ApiError(404, "agreement_not_found", "Accepted agreement not found.")
    if agreement.status != AgreementStatus.ACTIVE.value:
        raise ApiError(
            409,
            "agreement_not_accepted",
            "Both parties must accept the current agreement before escrow creation.",
        )
    existing = await session.scalar(select(Escrow).where(Escrow.agreement_id == agreement_id))
    if existing is not None:
        raise ApiError(409, "escrow_exists", "An escrow already exists for this agreement.")
    version = await session.scalar(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.version_number == agreement.current_version_number,
        )
    )
    if version is None:
        raise ApiError(
            409, "agreement_version_missing", "The accepted agreement version is unavailable."
        )
    terms_amount = Decimal(str(version.terms["budget_amount"]))
    # Credits are demo units only. The numeric agreement amount is used as a scenario
    # parameter, not as a conversion to fiat or a promise of monetary equivalence.
    amount = terms_amount.quantize(Decimal("0.01"))
    escrow_account = LedgerAccount(
        owner_user_id=None, account_type="escrow", currency="VCR", balance=Decimal("0")
    )
    session.add(escrow_account)
    await session.flush()
    escrow = Escrow(
        agreement_id=agreement.id,
        buyer_user_id=agreement.owner_user_id,
        agent_owner_user_id=agreement.agent_owner_user_id,
        escrow_account_id=escrow_account.id,
        amount=amount,
        currency="VCR",
        status="created",
    )
    session.add(escrow)
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.ESCROW_CREATED,
        subject_type="escrow",
        subject_id=escrow.id,
        actor_user_id=actor_id,
        metadata={
            "agreement_id": str(agreement.id),
            "amount": str(amount),
            "currency": "VCR",
            "simulated": True,
        },
    )
    await session.commit()
    return escrow


async def _get_participant_escrow(
    session: AsyncSession, escrow_id: UUID, actor_id: UUID, *, lock: bool = False
) -> Escrow:
    stmt = select(Escrow).where(
        Escrow.id == escrow_id,
        or_(Escrow.buyer_user_id == actor_id, Escrow.agent_owner_user_id == actor_id),
    )
    if lock:
        stmt = stmt.with_for_update()
    escrow = await session.scalar(stmt)
    if escrow is None:
        raise ApiError(404, "escrow_not_found", "Escrow not found.")
    return escrow


async def list_escrows(
    session: AsyncSession, *, actor_id: UUID, limit: int, offset: int
) -> tuple[list[Escrow], int]:
    filters = or_(Escrow.buyer_user_id == actor_id, Escrow.agent_owner_user_id == actor_id)
    total = int(await session.scalar(select(func.count()).select_from(Escrow).where(filters)) or 0)
    items = list(
        await session.scalars(
            select(Escrow)
            .where(filters)
            .order_by(Escrow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


async def get_escrow(session: AsyncSession, *, actor_id: UUID, escrow_id: UUID) -> Escrow:
    return await _get_participant_escrow(session, escrow_id, actor_id)


async def _existing_result(
    session: AsyncSession, *, kind: str, escrow: Escrow, idempotency_key: str
) -> LedgerTransaction | None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(idempotency_key))))
    existing = await session.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
    )
    if existing is not None and existing.request_hash != _hash_payload(
        kind, escrow.id, escrow.amount
    ):
        raise ApiError(
            409,
            "idempotency_conflict",
            "Idempotency key was already used for a different request.",
        )
    return existing


async def fund_escrow(
    session: AsyncSession, *, actor_id: UUID, escrow_id: UUID, idempotency_key: str
) -> Escrow:
    escrow = await _get_participant_escrow(session, escrow_id, actor_id, lock=True)
    if actor_id != escrow.buyer_user_id:
        raise ApiError(403, "buyer_only", "Only the buyer can fund this escrow.")
    if await _existing_result(
        session, kind="escrow_fund", escrow=escrow, idempotency_key=idempotency_key
    ):
        return escrow
    if escrow.status != "created":
        raise ApiError(409, "invalid_escrow_state", "Only a created escrow can be funded.")
    buyer = await _account(session, actor_id, "available", lock=True)
    holding = await session.get(LedgerAccount, escrow.escrow_account_id)
    if holding is None:
        raise ApiError(500, "ledger_account_missing", "Escrow holding account is unavailable.")
    await _post(
        session,
        actor_id=actor_id,
        kind="escrow_fund",
        amount=escrow.amount,
        source=buyer,
        destination=holding,
        idempotency_key=idempotency_key,
        escrow_id=escrow.id,
        description="Fund simulated escrow with virtual credits",
    )
    escrow.status = "funded"
    await session.commit()
    return escrow


async def _settle(
    session: AsyncSession, *, actor_id: UUID, escrow_id: UUID, idempotency_key: str, kind: str
) -> Escrow:
    escrow = await _get_participant_escrow(session, escrow_id, actor_id, lock=True)
    if kind == "escrow_release" and actor_id != escrow.agent_owner_user_id:
        raise ApiError(
            403, "beneficiary_only", "Only the agent owner can request the demo release."
        )
    if kind == "escrow_refund" and actor_id != escrow.buyer_user_id:
        raise ApiError(403, "buyer_only", "Only the buyer can request a demo refund.")
    if await _existing_result(session, kind=kind, escrow=escrow, idempotency_key=idempotency_key):
        return escrow
    if escrow.status != "funded":
        raise ApiError(409, "invalid_escrow_state", "Only a funded escrow can be settled.")
    if kind == "escrow_release":
        target = await _account(session, escrow.agent_owner_user_id, "available", lock=True)
        next_status = "released"
        description = "Release simulated escrow to agent owner"
    else:
        if actor_id != escrow.buyer_user_id:
            raise ApiError(403, "buyer_only", "Only the buyer can request a demo refund.")
        target = await _account(session, escrow.buyer_user_id, "available", lock=True)
        next_status = "refunded"
        description = "Refund simulated escrow to buyer"
    holding = await session.get(LedgerAccount, escrow.escrow_account_id)
    if holding is None:
        raise ApiError(500, "ledger_account_missing", "Escrow holding account is unavailable.")
    await _post(
        session,
        actor_id=actor_id,
        kind=kind,
        amount=escrow.amount,
        source=holding,
        destination=target,
        idempotency_key=idempotency_key,
        escrow_id=escrow.id,
        description=description,
    )
    escrow.status = next_status
    await session.commit()
    return escrow


async def release_escrow(
    session: AsyncSession, *, actor_id: UUID, escrow_id: UUID, idempotency_key: str
) -> Escrow:
    return await _settle(
        session,
        actor_id=actor_id,
        escrow_id=escrow_id,
        idempotency_key=idempotency_key,
        kind="escrow_release",
    )


async def refund_escrow(
    session: AsyncSession, *, actor_id: UUID, escrow_id: UUID, idempotency_key: str
) -> Escrow:
    return await _settle(
        session,
        actor_id=actor_id,
        escrow_id=escrow_id,
        idempotency_key=idempotency_key,
        kind="escrow_refund",
    )


async def provision_demo_credits(
    session: AsyncSession,
    *,
    actor_id: UUID,
    target_user_id: UUID,
    amount: Decimal,
    idempotency_key: str,
) -> LedgerTransaction:
    # This operation is wired only behind demo-mode + ADMIN permission in the API.
    if await session.get(User, target_user_id) is None:
        raise ApiError(404, "user_not_found", "Target user not found.")
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext("autoescrow-vcr-system-account")))
    )
    system = await session.scalar(
        select(LedgerAccount)
        .where(LedgerAccount.owner_user_id.is_(None), LedgerAccount.account_type == "system")
        .with_for_update()
    )
    if system is None:
        system = LedgerAccount(
            owner_user_id=None,
            account_type="system",
            currency="VCR",
            balance=Decimal("1000000000.00"),
        )
        session.add(system)
        await session.flush()
    destination = await _account(session, target_user_id, "available", lock=True)
    tx = await _post(
        session,
        actor_id=actor_id,
        kind="demo_credit",
        amount=amount,
        source=system,
        destination=destination,
        idempotency_key=idempotency_key,
        escrow_id=None,
        description="Admin-issued simulated demo credits; no monetary value",
    )
    await session.commit()
    return tx


async def get_balances(session: AsyncSession, *, actor_id: UUID) -> tuple[Decimal, Decimal]:
    accounts = list(
        await session.scalars(
            select(LedgerAccount).where(
                LedgerAccount.owner_user_id == actor_id,
                LedgerAccount.account_type == "available",
            )
        )
    )
    available = sum((account.balance for account in accounts), Decimal("0.00"))
    locked = await session.scalar(
        select(func.coalesce(func.sum(Escrow.amount), 0)).where(
            Escrow.buyer_user_id == actor_id, Escrow.status == "funded"
        )
    )
    return available, Decimal(str(locked or "0.00"))


async def ledger_history(
    session: AsyncSession, *, actor_id: UUID, limit: int, offset: int
) -> tuple[list[LedgerTransaction], int]:
    filters = LedgerTransaction.actor_user_id == actor_id
    total = int(
        await session.scalar(select(func.count()).select_from(LedgerTransaction).where(filters))
        or 0
    )
    items = list(
        await session.scalars(
            select(LedgerTransaction)
            .where(filters)
            .order_by(LedgerTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total
