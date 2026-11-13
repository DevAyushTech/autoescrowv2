from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.models.enums import AuthNoncePurpose
from autoescrow.models.identity import User, Wallet
from autoescrow.services.audit import AuditEventType, record_audit
from autoescrow.services.siwe import verify_and_consume_nonce


async def update_profile(
    session: AsyncSession,
    *,
    user: User,
    fields_set: set[str],
    display_name: str | None,
    email: str | None,
) -> User:
    if not fields_set:
        raise ApiError(400, "empty_update", "At least one profile field must be provided.")

    if "display_name" in fields_set:
        if display_name is None:
            raise ApiError(422, "invalid_display_name", "Display name is required.")
        user.display_name = display_name
    if "email" in fields_set:
        user.email = email.lower() if email else None

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "email_unavailable", "That email address is not available.") from exc

    await session.commit()
    return user


async def list_wallets(session: AsyncSession, *, user_id: UUID) -> list[Wallet]:
    result = await session.scalars(
        select(Wallet)
        .where(Wallet.user_id == user_id)
        .order_by(Wallet.is_primary.desc(), Wallet.created_at.asc())
    )
    return list(result)


async def link_wallet(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    message: str,
    signature: str,
    now: datetime | None = None,
) -> Wallet:
    verified = await verify_and_consume_nonce(
        session,
        settings,
        message=message,
        signature=signature,
        purpose=AuthNoncePurpose.WALLET_LINK,
        user_id=user_id,
        now=now,
    )
    address = verified.nonce.address
    chain_id = verified.nonce.chain_id

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:wallet_key, 0))"),
        {"wallet_key": f"{chain_id}:{address.lower()}"},
    )
    existing = await session.scalar(
        select(Wallet).where(Wallet.chain_id == chain_id, Wallet.address == address)
    )
    if existing is not None:
        # The signature was valid, so burn the nonce even though linking failed.
        await record_audit(
            session,
            event_type=AuditEventType.WALLET_LINK_REJECTED,
            subject_type="wallet",
            subject_id=existing.id,
            actor_user_id=user_id,
            metadata={
                "reason": "already_linked",
                "wallet_address": address,
                "chain_id": chain_id,
            },
        )
        await session.commit()
        raise ApiError(409, "wallet_already_linked", "This wallet is already linked.")

    wallet_count = await session.scalar(
        select(func.count()).select_from(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = Wallet(
        user_id=user_id,
        address=address,
        chain_id=chain_id,
        is_primary=(wallet_count or 0) == 0,
        verified_at=verified.nonce.consumed_at or datetime.now(UTC),
    )
    session.add(wallet)
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.WALLET_LINKED,
        subject_type="wallet",
        subject_id=wallet.id,
        actor_user_id=user_id,
        metadata={"wallet_address": address, "chain_id": chain_id},
    )
    await session.commit()
    return wallet


async def remove_wallet(
    session: AsyncSession,
    *,
    user_id: UUID,
    wallet_id: UUID,
) -> None:
    wallet = await session.scalar(
        select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user_id)
    )
    if wallet is None:
        raise ApiError(404, "wallet_not_found", "Wallet not found.")

    wallet_count = await session.scalar(
        select(func.count()).select_from(Wallet).where(Wallet.user_id == user_id)
    )
    if (wallet_count or 0) <= 1:
        raise ApiError(
            409,
            "last_wallet",
            "The last authentication wallet cannot be removed.",
        )

    replacement: Wallet | None = None
    if wallet.is_primary:
        replacement = await session.scalar(
            select(Wallet)
            .where(Wallet.user_id == user_id, Wallet.id != wallet.id)
            .order_by(Wallet.created_at.asc())
            .limit(1)
        )

    address = wallet.address
    chain_id = wallet.chain_id
    await session.delete(wallet)
    await session.flush()
    if replacement is not None:
        replacement.is_primary = True

    await record_audit(
        session,
        event_type=AuditEventType.WALLET_UNLINKED,
        subject_type="wallet",
        subject_id=wallet_id,
        actor_user_id=user_id,
        metadata={"wallet_address": address, "chain_id": chain_id},
    )
    await session.commit()
