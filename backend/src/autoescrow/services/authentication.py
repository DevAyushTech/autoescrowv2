from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.models.enums import AuthNoncePurpose, RoleCode, UserStatus
from autoescrow.models.identity import User, Wallet
from autoescrow.models.rbac import Role, UserRole
from autoescrow.services.audit import AuditEventType, record_audit
from autoescrow.services.auth_sessions import (
    SessionTokens,
    create_session_tokens,
    load_role_codes,
)
from autoescrow.services.siwe import verify_and_consume_nonce


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    roles: frozenset[str]
    tokens: SessionTokens


async def _lock_wallet_address(session: AsyncSession, *, chain_id: int, address: str) -> None:
    """Serialize first-login creation for a wallet within this transaction."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:wallet_key, 0))"),
        {"wallet_key": f"{chain_id}:{address.lower()}"},
    )


async def _user_role(session: AsyncSession) -> Role:
    role = await session.scalar(select(Role).where(Role.code == RoleCode.USER.value))
    if role is None:
        raise RuntimeError("USER role is missing; database migrations were not applied")
    return role


async def login_with_siwe(
    session: AsyncSession,
    settings: Settings,
    *,
    message: str,
    signature: str,
    now: datetime | None = None,
) -> LoginResult:
    verified = await verify_and_consume_nonce(
        session,
        settings,
        message=message,
        signature=signature,
        purpose=AuthNoncePurpose.LOGIN,
        now=now,
    )
    nonce = verified.nonce
    address = nonce.address
    chain_id = nonce.chain_id

    await _lock_wallet_address(session, chain_id=chain_id, address=address)
    wallet = await session.scalar(
        select(Wallet).where(Wallet.chain_id == chain_id, Wallet.address == address)
    )

    if wallet is not None:
        user = await session.get(User, wallet.user_id)
        if user is None:
            raise RuntimeError("wallet references a missing user")
    else:
        user = User(
            display_name=f"User {address[:6]}...{address[-4:]}",
            status=UserStatus.ACTIVE.value,
        )
        session.add(user)
        await session.flush()

        wallet = Wallet(
            user_id=user.id,
            address=address,
            chain_id=chain_id,
            is_primary=True,
            verified_at=nonce.consumed_at or datetime.now(UTC),
        )
        session.add(wallet)
        session.add(
            UserRole(
                user_id=user.id,
                role_id=(await _user_role(session)).id,
            )
        )
        await session.flush()

    if user.status != UserStatus.ACTIVE.value:
        await record_audit(
            session,
            event_type=AuditEventType.LOGIN_DENIED,
            subject_type="user",
            subject_id=user.id,
            actor_user_id=user.id,
            metadata={"reason": "account_status", "wallet_address": address},
        )
        await session.commit()
        raise ApiError(403, "account_unavailable", "This account cannot authenticate.")

    roles = await load_role_codes(session, user.id)
    tokens = await create_session_tokens(
        session,
        settings,
        user_id=user.id,
        roles=roles,
        now=now,
    )
    await record_audit(
        session,
        event_type=AuditEventType.LOGIN_SUCCEEDED,
        subject_type="user",
        subject_id=user.id,
        actor_user_id=user.id,
        metadata={
            "wallet_address": address,
            "chain_id": chain_id,
            "session_id": str(tokens.session.id),
        },
    )
    await session.commit()
    return LoginResult(user=user, roles=frozenset(roles), tokens=tokens)
