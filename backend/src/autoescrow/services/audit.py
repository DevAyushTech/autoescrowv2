from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.models.audit import AuditEvent


class AuditEventType(StrEnum):
    NONCE_ISSUED = "auth.nonce_issued"
    SIGNATURE_VERIFIED = "auth.signature_verified"
    SIGNATURE_FAILED = "auth.signature_failed"
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_DENIED = "auth.login_denied"
    TOKEN_REFRESHED = "auth.token_refreshed"
    REFRESH_REUSE_DETECTED = "auth.refresh_reuse_detected"
    TOKEN_REFRESH_FAILED = "auth.token_refresh_failed"
    LOGOUT = "auth.logout"
    SESSION_REVOKED = "auth.session_revoked"
    WALLET_LINKED = "wallet.linked"
    WALLET_LINK_REJECTED = "wallet.link_rejected"
    WALLET_UNLINKED = "wallet.unlinked"
    ROLE_ASSIGNED = "rbac.role_assigned"
    ROLE_REVOKED = "rbac.role_revoked"
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_DEACTIVATED = "agent.deactivated"
    JOB_CREATED = "job.created"
    JOB_UPDATED = "job.updated"
    JOB_CANCELLED = "job.cancelled"
    AGREEMENT_CREATED = "agreement.created"
    AGREEMENT_VERSION_CREATED = "agreement.version_created"
    AGREEMENT_ACCEPTED = "agreement.accepted"
    AGREEMENT_ACTIVATED = "agreement.activated"
    ESCROW_CREATED = "escrow.created"
    ESCROW_FUNDED = "escrow.funded"
    ESCROW_RELEASED = "escrow.released"
    ESCROW_REFUNDED = "escrow.refunded"
    LEDGER_DEMO_CREDIT = "ledger.demo_credit"


async def record_audit(
    session: AsyncSession,
    *,
    event_type: AuditEventType,
    subject_type: str,
    subject_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one non-sensitive audit event within the caller's transaction."""

    event = AuditEvent(
        event_type=event_type.value,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_user_id=actor_user_id,
        event_metadata=metadata or {},
    )
    session.add(event)
    await session.flush()
    return event
