from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission, has_permission
from autoescrow.infrastructure.database import Database
from autoescrow.infrastructure.rate_limit import RateLimiter
from autoescrow.models.enums import UserStatus
from autoescrow.models.identity import User
from autoescrow.services.auth_sessions import get_active_session, load_role_codes
from autoescrow.services.tokens import AccessTokenError, decode_access_token

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Short-lived JWT access token issued by the wallet login endpoint.",
)


def _bearer_error(code: str, message: str) -> ApiError:
    return ApiError(
        401,
        code,
        message,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session


async def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    session_id: UUID
    roles: frozenset[str]

    @property
    def user_id(self) -> UUID:
        return self.user.id


async def authenticate_bearer(
    credentials: HTTPAuthorizationCredentials,
    session: AsyncSession,
    settings: Settings,
) -> AuthenticatedUser:
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except AccessTokenError as exc:
        raise _bearer_error("invalid_access_token", "Access token is invalid.") from exc

    refresh_session = await get_active_session(
        session,
        session_id=claims.session_id,
        user_id=claims.user_id,
    )
    if refresh_session is None:
        raise _bearer_error("invalid_access_token", "Access token is invalid.")

    user = await session.get(User, claims.user_id)
    if user is None:
        raise _bearer_error("invalid_access_token", "Access token is invalid.")
    if user.status != UserStatus.ACTIVE.value:
        raise ApiError(403, "account_unavailable", "This account cannot authenticate.")

    roles = await load_role_codes(session, user.id)
    return AuthenticatedUser(user=user, session_id=claims.session_id, roles=frozenset(roles))


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    session: DbSession,
    settings: SettingsDep,
) -> AuthenticatedUser:
    if credentials is None:
        raise _bearer_error("authentication_required", "Authentication is required.")
    return await authenticate_bearer(credentials, session, settings)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_principal)]


async def get_optional_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    session: DbSession,
    settings: SettingsDep,
) -> AuthenticatedUser | None:
    """Authenticate when a Bearer token is present, otherwise allow public access."""

    if credentials is None:
        return None
    return await authenticate_bearer(credentials, session, settings)


OptionalUser = Annotated[AuthenticatedUser | None, Depends(get_optional_principal)]


def require_permission(
    permission: Permission,
) -> Callable[..., Awaitable[AuthenticatedUser]]:
    async def dependency(principal: CurrentUser) -> AuthenticatedUser:
        if not has_permission(principal.roles, permission):
            raise ApiError(403, "forbidden", "You do not have permission to perform this action.")
        return principal

    return dependency


class RateLimitScope(StrEnum):
    NONCE = "nonce"
    VERIFY = "verify"
    REFRESH = "refresh"
    SESSION = "session"
    WALLET = "wallet"


def _limit_for_scope(settings: Settings, scope: RateLimitScope) -> int:
    match scope:
        case RateLimitScope.NONCE:
            return settings.rate_limit_nonce_requests
        case RateLimitScope.VERIFY:
            return settings.rate_limit_verify_requests
        case RateLimitScope.REFRESH:
            return settings.rate_limit_refresh_requests
        case RateLimitScope.SESSION:
            return settings.rate_limit_session_requests
        case RateLimitScope.WALLET:
            return settings.rate_limit_wallet_requests


def rate_limit(scope: RateLimitScope) -> Callable[..., Awaitable[None]]:
    async def dependency(
        request: Request,
        settings: SettingsDep,
        limiter: RateLimiterDep,
    ) -> None:
        identity = request.client.host if request.client else "unknown"
        await limiter.enforce(scope.value, identity, limit=_limit_for_scope(settings, scope))

    return dependency
