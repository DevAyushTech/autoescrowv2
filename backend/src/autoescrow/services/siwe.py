import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils.address import is_address, to_checksum_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.infrastructure.siwe_library import ISO8601Datetime, SiweMessage
from autoescrow.models.enums import AuthNoncePurpose
from autoescrow.models.identity import AuthNonce
from autoescrow.services.audit import AuditEventType, record_audit

logger = logging.getLogger("autoescrow.siwe")

_HEX_SIGNATURE = re.compile(r"^[0-9a-fA-F]{130}$")


@dataclass(frozen=True, slots=True)
class NonceChallenge:
    address: str
    chain_id: int
    nonce: str
    message: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedNonce:
    nonce: AuthNonce


def _utc_now(now: datetime | None = None) -> datetime:
    resolved = now or datetime.now(UTC)
    if resolved.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    resolved = resolved.astimezone(UTC)
    return resolved.replace(microsecond=(resolved.microsecond // 1000) * 1000)


def normalize_wallet_address(value: str) -> str:
    if not is_address(value):
        raise ApiError(422, "invalid_wallet_address", "A valid Ethereum address is required.")
    return to_checksum_address(value)


def validate_chain(chain_id: int, settings: Settings) -> None:
    if chain_id != settings.ethereum_chain_id:
        raise ApiError(422, "unsupported_chain", "The requested chain is not supported.")


def _build_message(
    settings: Settings,
    *,
    address: str,
    chain_id: int,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    message = SiweMessage(
        domain=settings.siwe_domain,
        address=address,
        uri=settings.siwe_uri,
        version="1",
        chain_id=chain_id,
        issued_at=ISO8601Datetime.from_datetime(issued_at),
        expiration_time=ISO8601Datetime.from_datetime(expires_at),
        nonce=nonce,
        statement=settings.siwe_statement,
    )
    return message.prepare_message()


def _parse_siwe_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("SIWE datetime must include a timezone")
    return parsed.astimezone(UTC)


def _recover_signer(message: str, signature: str) -> str:
    normalized = signature.strip()
    if normalized.startswith(("0x", "0X")):
        normalized = normalized[2:]
    if not _HEX_SIGNATURE.fullmatch(normalized):
        raise ValueError("signature must be a 65-byte hexadecimal value")

    signable = encode_defunct(text=message)
    recovered = Account.recover_message(signable, signature=bytes.fromhex(normalized))
    return to_checksum_address(recovered)


async def _fail(
    session: AsyncSession,
    *,
    reason: str,
    address: str | None = None,
    nonce: AuthNonce | None = None,
    user_id: UUID | None = None,
) -> None:
    """Persist a failure audit in its own transaction, then raise."""

    nonce_id = nonce.id if nonce else None
    metadata = {"reason": reason}
    if address:
        metadata["wallet_address"] = address

    await session.rollback()
    await record_audit(
        session,
        event_type=AuditEventType.SIGNATURE_FAILED,
        subject_type="auth_nonce",
        subject_id=nonce_id,
        actor_user_id=user_id,
        metadata=metadata,
    )
    await session.commit()


def _authentication_error(code: str) -> ApiError:
    return ApiError(401, code, "Authentication failed.")


async def issue_nonce(
    session: AsyncSession,
    settings: Settings,
    *,
    address: str,
    chain_id: int,
    purpose: AuthNoncePurpose,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> NonceChallenge:
    normalized_address = normalize_wallet_address(address)
    validate_chain(chain_id, settings)
    if purpose is AuthNoncePurpose.WALLET_LINK and user_id is None:
        raise ApiError(401, "authentication_required", "Authentication is required.")
    if purpose is AuthNoncePurpose.LOGIN and user_id is not None:
        raise ApiError(400, "invalid_nonce_purpose", "Login nonces are not user-bound.")

    issued_at = _utc_now(now)
    expires_at = issued_at + timedelta(seconds=settings.auth_nonce_ttl_seconds)
    nonce_value = secrets.token_hex(16)
    message = _build_message(
        settings,
        address=normalized_address,
        chain_id=chain_id,
        nonce=nonce_value,
        issued_at=issued_at,
        expires_at=expires_at,
    )

    nonce = AuthNonce(
        user_id=user_id,
        address=normalized_address,
        chain_id=chain_id,
        nonce=nonce_value,
        purpose=purpose.value,
        message=message,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    session.add(nonce)
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.NONCE_ISSUED,
        subject_type="auth_nonce",
        subject_id=nonce.id,
        actor_user_id=user_id,
        metadata={
            "wallet_address": normalized_address,
            "chain_id": chain_id,
            "purpose": purpose.value,
        },
    )
    await session.commit()

    return NonceChallenge(
        address=normalized_address,
        chain_id=chain_id,
        nonce=nonce_value,
        message=message,
        issued_at=issued_at,
        expires_at=expires_at,
    )


async def verify_and_consume_nonce(
    session: AsyncSession,
    settings: Settings,
    *,
    message: str,
    signature: str,
    purpose: AuthNoncePurpose,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> VerifiedNonce:
    current_time = _utc_now(now)

    try:
        parsed = SiweMessage.from_message(message)
    except ValueError as exc:
        await _fail(session, reason="malformed_message", user_id=user_id)
        raise _authentication_error("invalid_siwe_message") from exc

    nonce = await session.scalar(
        select(AuthNonce)
        .where(AuthNonce.nonce == parsed.nonce, AuthNonce.purpose == purpose.value)
        .with_for_update()
    )
    if nonce is None:
        await _fail(
            session,
            reason="unknown_nonce",
            address=str(parsed.address),
            user_id=user_id,
        )
        raise _authentication_error("invalid_nonce")

    if purpose is AuthNoncePurpose.WALLET_LINK and nonce.user_id != user_id:
        await _fail(
            session,
            reason="nonce_user_mismatch",
            address=nonce.address,
            nonce=nonce,
        )
        raise _authentication_error("invalid_nonce")

    if nonce.consumed_at is not None:
        await _fail(session, reason="nonce_consumed", address=nonce.address, nonce=nonce)
        raise _authentication_error("nonce_already_used")

    if nonce.expires_at <= current_time:
        await _fail(session, reason="nonce_expired", address=nonce.address, nonce=nonce)
        raise _authentication_error("nonce_expired")

    if parsed.domain != settings.siwe_domain:
        await _fail(session, reason="domain_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_domain_mismatch")

    if str(parsed.uri) != settings.siwe_uri:
        await _fail(session, reason="uri_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_uri_mismatch")

    if parsed.chain_id != settings.ethereum_chain_id or parsed.chain_id != nonce.chain_id:
        await _fail(session, reason="chain_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_chain_mismatch")

    if str(parsed.address) != nonce.address:
        await _fail(session, reason="wallet_mismatch", address=str(parsed.address), nonce=nonce)
        raise _authentication_error("siwe_wallet_mismatch")

    if parsed.statement != settings.siwe_statement:
        await _fail(session, reason="statement_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_statement_mismatch")

    if message != nonce.message:
        await _fail(session, reason="message_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_message_mismatch")

    try:
        issued_at = _parse_siwe_datetime(str(parsed.issued_at))
        expiration_time = _parse_siwe_datetime(
            str(parsed.expiration_time) if parsed.expiration_time else None
        )
        not_before = _parse_siwe_datetime(str(parsed.not_before) if parsed.not_before else None)
    except ValueError as exc:
        await _fail(session, reason="invalid_timestamp", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_timestamp_invalid") from exc

    if issued_at is None or expiration_time is None:
        await _fail(session, reason="missing_timestamp", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_timestamp_missing")

    latest_issue_time = current_time + timedelta(seconds=settings.auth_clock_skew_seconds)
    if issued_at > latest_issue_time:
        await _fail(session, reason="issued_in_future", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_issued_at_invalid")
    if issued_at < nonce.issued_at - timedelta(seconds=settings.auth_clock_skew_seconds):
        await _fail(session, reason="issued_too_early", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_issued_at_invalid")
    if expiration_time <= current_time:
        await _fail(session, reason="message_expired", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_message_expired")
    if expiration_time > nonce.expires_at:
        await _fail(session, reason="message_outlives_nonce", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_expiration_invalid")
    if expiration_time <= issued_at:
        await _fail(session, reason="expiration_before_issue", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_expiration_invalid")
    if not_before is not None and not_before > latest_issue_time:
        await _fail(session, reason="not_yet_valid", address=nonce.address, nonce=nonce)
        raise _authentication_error("siwe_not_yet_valid")

    try:
        recovered_address = _recover_signer(message, signature)
    except (ValueError, TypeError) as exc:
        await _fail(session, reason="invalid_signature", address=nonce.address, nonce=nonce)
        raise _authentication_error("invalid_signature") from exc

    if recovered_address != nonce.address:
        await _fail(session, reason="signature_wallet_mismatch", address=nonce.address, nonce=nonce)
        raise _authentication_error("invalid_signature")

    nonce.consumed_at = current_time
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.SIGNATURE_VERIFIED,
        subject_type="auth_nonce",
        subject_id=nonce.id,
        actor_user_id=user_id,
        metadata={
            "wallet_address": nonce.address,
            "chain_id": nonce.chain_id,
            "purpose": purpose.value,
        },
    )
    return VerifiedNonce(nonce=nonce)
