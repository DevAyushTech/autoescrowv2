from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, Security
from fastapi.security import HTTPAuthorizationCredentials

from autoescrow.api.dependencies import (
    CurrentUser,
    DbSession,
    RateLimitScope,
    SettingsDep,
    authenticate_bearer,
    bearer_scheme,
    rate_limit,
    require_permission,
)
from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission, has_permission
from autoescrow.models.enums import AuthNoncePurpose
from autoescrow.schemas.auth import (
    LoginResponse,
    NonceRequest,
    NonceResponse,
    RefreshRequest,
    TokenResponse,
    VerifyRequest,
)
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.sessions import SessionListResponse, SessionResponse
from autoescrow.schemas.users import UserResponse
from autoescrow.services.auth_sessions import (
    list_active_sessions,
    logout_session,
    revoke_session_by_id,
    rotate_refresh_token,
)
from autoescrow.services.authentication import login_with_siwe
from autoescrow.services.siwe import issue_nonce

router = APIRouter(prefix="/auth", tags=["authentication"])

_NONCE_DESCRIPTION = """
Create a one-time EIP-4361 message. The client signs the returned `message`
exactly as provided and submits it to `/auth/wallet/verify`.

`purpose=login` is public. `purpose=wallet_link` requires a Bearer access token
and binds the nonce to that authenticated user.
"""

_VERIFY_DESCRIPTION = """
Verify the exact SIWE message and its EIP-191 signature, consume the nonce once,
find or create the user and wallet, and issue an access/refresh token pair.

The access token is sent as `Authorization: Bearer <token>`. The opaque refresh
token is returned only in the JSON body and must be sent to the refresh endpoint.
"""


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post(
    "/wallet/nonce",
    response_model=NonceResponse,
    summary="Create a Sign-In with Ethereum challenge",
    description=_NONCE_DESCRIPTION,
    responses=ERROR_RESPONSES,
)
async def create_wallet_nonce(
    payload: NonceRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    _nonce_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.NONCE))],
) -> NonceResponse:
    user_id: UUID | None = None
    if payload.purpose is AuthNoncePurpose.WALLET_LINK:
        if credentials is None:
            raise ApiError(
                401,
                "authentication_required",
                "Authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        principal = await authenticate_bearer(credentials, session, settings)
        if not has_permission(principal.roles, Permission.WALLET_WRITE):
            raise ApiError(403, "forbidden", "You do not have permission to link a wallet.")
        user_id = principal.user_id

    challenge = await issue_nonce(
        session,
        settings,
        address=payload.address,
        chain_id=payload.chain_id,
        purpose=payload.purpose,
        user_id=user_id,
    )
    _no_store(response)
    return NonceResponse(
        address=challenge.address,
        chain_id=challenge.chain_id,
        purpose=payload.purpose,
        nonce=challenge.nonce,
        message=challenge.message,
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
    )


@router.post(
    "/wallet/verify",
    response_model=LoginResponse,
    summary="Verify a SIWE signature and start a session",
    description=_VERIFY_DESCRIPTION,
    responses=ERROR_RESPONSES,
)
async def verify_wallet_signature(
    payload: VerifyRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _verify_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.VERIFY))],
) -> LoginResponse:
    result = await login_with_siwe(
        session,
        settings,
        message=payload.message,
        signature=payload.signature,
    )
    _no_store(response)
    return LoginResponse(
        access_token=result.tokens.access_token.value,
        access_token_expires_at=result.tokens.access_token.expires_at,
        refresh_token=result.tokens.refresh_token,
        refresh_token_expires_at=result.tokens.refresh_expires_at,
        user=UserResponse.from_user(result.user, result.roles),
    )


@router.post(
    "/token/refresh",
    response_model=TokenResponse,
    summary="Rotate an opaque refresh token",
    description=(
        "Every successful refresh revokes the presented token and returns a new one. "
        "Reuse of a rotated token revokes the entire session family."
    ),
    responses=ERROR_RESPONSES,
)
async def refresh_tokens(
    payload: RefreshRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _refresh_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.REFRESH))],
) -> TokenResponse:
    rotated = await rotate_refresh_token(
        session,
        settings,
        raw_refresh_token=payload.refresh_token,
    )
    _no_store(response)
    return TokenResponse(
        access_token=rotated.access_token.value,
        access_token_expires_at=rotated.access_token.expires_at,
        refresh_token=rotated.refresh_token,
        refresh_token_expires_at=rotated.refresh_expires_at,
    )


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    summary="Revoke the current session family",
    responses=ERROR_RESPONSES,
)
async def logout(
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.SESSION_REVOKE)),
    ],
    session: DbSession,
    _session_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.SESSION))],
) -> Response:
    await logout_session(
        session,
        user_id=principal.user_id,
        session_id=principal.session_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List active sessions",
    responses=ERROR_RESPONSES,
)
async def list_sessions(
    response: Response,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.SESSION_READ)),
    ],
    session: DbSession,
    _session_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.SESSION))],
) -> SessionListResponse:
    active = await list_active_sessions(
        session,
        user_id=principal.user_id,
        current_session_id=principal.session_id,
    )
    _no_store(response)
    return SessionListResponse(
        sessions=[
            SessionResponse.from_session(item, current=is_current) for item, is_current in active
        ]
    )


@router.post(
    "/sessions/{session_id}/revoke",
    status_code=204,
    response_class=Response,
    summary="Revoke a session family",
    responses=ERROR_RESPONSES,
)
async def revoke_session(
    session_id: UUID,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.SESSION_REVOKE)),
    ],
    session: DbSession,
    _session_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.SESSION))],
) -> Response:
    await revoke_session_by_id(
        session,
        user_id=principal.user_id,
        session_id=session_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
