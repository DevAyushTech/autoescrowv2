import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.models.enums import RefreshRevocationReason, UserStatus
from autoescrow.models.identity import RefreshSession, User
from autoescrow.models.rbac import Role, UserRole
from autoescrow.services.audit import AuditEventType, record_audit
from autoescrow.services.tokens import AccessToken, create_access_token


@dataclass(frozen=True, slots=True)
class SessionTokens:
    refresh_token: str
    refresh_expires_at: datetime
    access_token: AccessToken
    session: RefreshSession


def _utc_now(now: datetime | None = None) -> datetime:
    resolved = now or datetime.now(UTC)
    if resolved.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return resolved.astimezone(UTC).replace(microsecond=0)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


async def load_role_codes(session: AsyncSession, user_id: UUID) -> set[str]:
    result = await session.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return set(result)


async def create_session_tokens(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    roles: set[str],
    family_id: UUID | None = None,
    now: datetime | None = None,
) -> SessionTokens:
    issued_at = _utc_now(now)
    raw_refresh_token = generate_refresh_token()
    refresh_session = RefreshSession(
        user_id=user_id,
        token_hash=hash_refresh_token(raw_refresh_token),
        family_id=family_id or uuid4(),
        expires_at=issued_at + timedelta(seconds=settings.refresh_token_ttl_seconds),
        created_at=issued_at,
    )
    session.add(refresh_session)
    await session.flush()

    access_token = create_access_token(
        settings,
        user_id=user_id,
        session_id=refresh_session.id,
        roles=roles,
        now=issued_at,
    )
    return SessionTokens(
        refresh_token=raw_refresh_token,
        refresh_expires_at=refresh_session.expires_at,
        access_token=access_token,
        session=refresh_session,
    )


async def get_active_session(
    session: AsyncSession,
    *,
    session_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> RefreshSession | None:
    current_time = _utc_now(now)
    refresh_session = await session.scalar(
        select(RefreshSession).where(
            RefreshSession.id == session_id,
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > current_time,
        )
    )
    return refresh_session


async def revoke_family(
    session: AsyncSession,
    *,
    family_id: UUID,
    reason: RefreshRevocationReason,
    now: datetime | None = None,
) -> list[UUID]:
    revoked_at = _utc_now(now)
    result = await session.scalars(
        update(RefreshSession)
        .where(
            RefreshSession.family_id == family_id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at, revocation_reason=reason.value)
        .returning(RefreshSession.id)
    )
    return list(result)


async def rotate_refresh_token(
    session: AsyncSession,
    settings: Settings,
    *,
    raw_refresh_token: str,
    now: datetime | None = None,
) -> SessionTokens:
    current_time = _utc_now(now)
    token_hash = hash_refresh_token(raw_refresh_token)
    existing = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == token_hash).with_for_update()
    )

    if existing is None:
        await record_audit(
            session,
            event_type=AuditEventType.TOKEN_REFRESH_FAILED,
            subject_type="refresh_session",
            metadata={"reason": "unknown_token"},
        )
        await session.commit()
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid.")

    if existing.revoked_at is not None:
        revoked_ids = await revoke_family(
            session,
            family_id=existing.family_id,
            reason=RefreshRevocationReason.REUSE_DETECTED,
            now=current_time,
        )
        await record_audit(
            session,
            event_type=AuditEventType.REFRESH_REUSE_DETECTED,
            subject_type="refresh_session",
            subject_id=existing.id,
            actor_user_id=existing.user_id,
            metadata={"revoked_session_count": len(revoked_ids)},
        )
        await session.commit()
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid.")

    if existing.expires_at <= current_time:
        await record_audit(
            session,
            event_type=AuditEventType.TOKEN_REFRESH_FAILED,
            subject_type="refresh_session",
            subject_id=existing.id,
            actor_user_id=existing.user_id,
            metadata={"reason": "expired"},
        )
        await session.commit()
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid.")

    user = await session.get(User, existing.user_id)
    if user is None:
        await session.rollback()
        raise ApiError(401, "invalid_refresh_token", "Refresh token is invalid.")

    if user.status != UserStatus.ACTIVE.value:
        await revoke_family(
            session,
            family_id=existing.family_id,
            reason=RefreshRevocationReason.USER_REVOKED,
            now=current_time,
        )
        await record_audit(
            session,
            event_type=AuditEventType.TOKEN_REFRESH_FAILED,
            subject_type="refresh_session",
            subject_id=existing.id,
            actor_user_id=user.id,
            metadata={"reason": "account_unavailable"},
        )
        await session.commit()
        raise ApiError(403, "account_unavailable", "This account cannot authenticate.")

    roles = await load_role_codes(session, user.id)
    rotated = await create_session_tokens(
        session,
        settings,
        user_id=user.id,
        roles=roles,
        family_id=existing.family_id,
        now=current_time,
    )

    existing.revoked_at = current_time
    existing.revocation_reason = RefreshRevocationReason.ROTATED.value
    existing.replaced_by_session_id = rotated.session.id
    await record_audit(
        session,
        event_type=AuditEventType.TOKEN_REFRESHED,
        subject_type="refresh_session",
        subject_id=rotated.session.id,
        actor_user_id=user.id,
        metadata={"previous_session_id": str(existing.id)},
    )
    await session.commit()
    return rotated


async def list_active_sessions(
    session: AsyncSession,
    *,
    user_id: UUID,
    current_session_id: UUID,
    now: datetime | None = None,
) -> list[tuple[RefreshSession, bool]]:
    current_time = _utc_now(now)
    result = await session.scalars(
        select(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > current_time,
        )
        .order_by(RefreshSession.created_at.desc())
    )
    return [(row, row.id == current_session_id) for row in result]


async def revoke_session_by_id(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    current_time = _utc_now(now)
    target = await session.scalar(
        select(RefreshSession).where(
            RefreshSession.id == session_id,
            RefreshSession.user_id == user_id,
        )
    )
    if target is None:
        raise ApiError(404, "session_not_found", "Session not found.")

    revoked_ids = await revoke_family(
        session,
        family_id=target.family_id,
        reason=RefreshRevocationReason.USER_REVOKED,
        now=current_time,
    )
    await record_audit(
        session,
        event_type=AuditEventType.SESSION_REVOKED,
        subject_type="refresh_session",
        subject_id=target.id,
        actor_user_id=user_id,
        metadata={"revoked_session_count": len(revoked_ids)},
    )
    await session.commit()


async def logout_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    current_time = _utc_now(now)
    target = await session.scalar(
        select(RefreshSession).where(
            RefreshSession.id == session_id,
            RefreshSession.user_id == user_id,
        )
    )
    if target is None:
        return

    revoked_ids = await revoke_family(
        session,
        family_id=target.family_id,
        reason=RefreshRevocationReason.LOGOUT,
        now=current_time,
    )
    await record_audit(
        session,
        event_type=AuditEventType.LOGOUT,
        subject_type="refresh_session",
        subject_id=target.id,
        actor_user_id=user_id,
        metadata={"revoked_session_count": len(revoked_ids)},
    )
    await session.commit()
