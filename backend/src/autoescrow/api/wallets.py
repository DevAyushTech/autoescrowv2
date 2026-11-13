from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from autoescrow.api.dependencies import (
    CurrentUser,
    DbSession,
    RateLimitScope,
    SettingsDep,
    rate_limit,
    require_permission,
)
from autoescrow.core.permissions import Permission
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.wallets import WalletLinkRequest, WalletListResponse, WalletResponse
from autoescrow.services.users import link_wallet, list_wallets, remove_wallet

router = APIRouter(prefix="/users/me/wallets", tags=["wallets"])


@router.get(
    "",
    response_model=WalletListResponse,
    summary="List the authenticated user's wallets",
    responses=ERROR_RESPONSES,
)
async def get_wallets(
    response: Response,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.WALLET_READ)),
    ],
    session: DbSession,
) -> WalletListResponse:
    wallets = await list_wallets(session, user_id=principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    return WalletListResponse(wallets=[WalletResponse.from_wallet(wallet) for wallet in wallets])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WalletResponse,
    summary="Link a wallet using a signed challenge",
    responses=ERROR_RESPONSES,
)
async def add_wallet(
    payload: WalletLinkRequest,
    response: Response,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.WALLET_WRITE)),
    ],
    session: DbSession,
    settings: SettingsDep,
    _wallet_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.WALLET))],
) -> WalletResponse:
    wallet = await link_wallet(
        session,
        settings,
        user_id=principal.user_id,
        message=payload.message,
        signature=payload.signature,
    )
    response.headers["Cache-Control"] = "no-store"
    return WalletResponse.from_wallet(wallet)


@router.delete(
    "/{wallet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Unlink a wallet",
    responses=ERROR_RESPONSES,
)
async def delete_wallet(
    wallet_id: UUID,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.WALLET_WRITE)),
    ],
    session: DbSession,
    _wallet_rate_limit: Annotated[None, Depends(rate_limit(RateLimitScope.WALLET))],
) -> Response:
    await remove_wallet(
        session,
        user_id=principal.user_id,
        wallet_id=wallet_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
