from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt

from autoescrow.core.config import Settings


class AccessTokenError(Exception):
    """Raised when an access token is missing, invalid, expired or mis-scoped."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: str
    expires_at: datetime
    token_id: str


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    session_id: UUID
    token_id: str
    roles: frozenset[str]
    issued_at: datetime
    expires_at: datetime


def _now_utc(now: datetime | None) -> datetime:
    resolved = now or datetime.now(UTC)
    if resolved.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return resolved.astimezone(UTC).replace(microsecond=0)


def create_access_token(
    settings: Settings,
    *,
    user_id: UUID,
    session_id: UUID,
    roles: set[str],
    now: datetime | None = None,
) -> AccessToken:
    issued_at = _now_utc(now)
    expires_at = issued_at + timedelta(seconds=settings.access_token_ttl_seconds)
    token_id = str(uuid4())
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": token_id,
        "type": "access",
        "roles": sorted(roles),
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        headers={"typ": "JWT"},
    )
    return AccessToken(value=token, expires_at=expires_at, token_id=token_id)


def decode_access_token(
    settings: Settings,
    token: str,
    *,
    now: datetime | None = None,
) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require": [
                    "exp",
                    "iat",
                    "nbf",
                    "iss",
                    "aud",
                    "sub",
                    "sid",
                    "jti",
                    "type",
                    "roles",
                ]
            },
        )
    except jwt.PyJWTError as exc:
        raise AccessTokenError("invalid access token") from exc

    if payload.get("type") != "access":
        raise AccessTokenError("invalid token type")

    subject = payload.get("sub")
    session_id = payload.get("sid")
    token_id = payload.get("jti")
    roles = payload.get("roles")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")

    if not isinstance(subject, str) or not isinstance(session_id, str):
        raise AccessTokenError("invalid token subject")
    if not isinstance(token_id, str) or not token_id:
        raise AccessTokenError("invalid token id")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise AccessTokenError("invalid token roles")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise AccessTokenError("invalid token timestamps")

    current_time = _now_utc(now)
    if issued_at > int(current_time.timestamp()) + settings.auth_clock_skew_seconds:
        raise AccessTokenError("token issued in the future")

    try:
        user_id = UUID(subject)
        parsed_session_id = UUID(session_id)
    except ValueError as exc:
        raise AccessTokenError("invalid token identifiers") from exc

    return AccessTokenClaims(
        user_id=user_id,
        session_id=parsed_session_id,
        token_id=token_id,
        roles=frozenset(roles),
        issued_at=datetime.fromtimestamp(issued_at, tz=UTC),
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )
