from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from autoescrow.api.dependencies import CurrentUser, DbSession, SettingsDep, require_permission
from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.ledger import (
    DemoCreditRequest,
    EscrowActionRequest,
    EscrowCreateRequest,
    EscrowListResponse,
    EscrowResponse,
    LedgerBalanceResponse,
    LedgerHistoryResponse,
    LedgerTransactionResponse,
)
from autoescrow.services.ledger import (
    create_escrow,
    fund_escrow,
    get_balances,
    get_escrow,
    ledger_history,
    list_escrows,
    provision_demo_credits,
    refund_escrow,
    release_escrow,
)

router = APIRouter(tags=["simulated-escrow-ledger"])


def _escrow_response(item: object) -> EscrowResponse:
    return EscrowResponse.model_validate(item)


@router.post(
    "/escrows",
    response_model=EscrowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a simulated escrow from a mutually accepted agreement",
    description=(
        "Creates a virtual-credit escrow. No payment, blockchain transaction, "
        "or real monetary value is involved."
    ),
    responses=ERROR_RESPONSES,
)
async def create_new_escrow(
    payload: EscrowCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
) -> EscrowResponse:
    item = await create_escrow(
        session, actor_id=principal.user_id, agreement_id=payload.agreement_id
    )
    response.headers["Cache-Control"] = "no-store"
    return _escrow_response(item)


@router.get(
    "/escrows",
    response_model=EscrowListResponse,
    summary="List the authenticated user's simulated escrows",
)
async def list_my_escrows(
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EscrowListResponse:
    items, total = await list_escrows(
        session, actor_id=principal.user_id, limit=limit, offset=offset
    )
    response.headers["Cache-Control"] = "no-store"
    return EscrowListResponse(
        items=[_escrow_response(item) for item in items], total=total, limit=limit, offset=offset
    )


@router.get("/escrows/{escrow_id}", response_model=EscrowResponse, summary="Get a simulated escrow")
async def get_my_escrow(
    escrow_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
) -> EscrowResponse:
    item = await get_escrow(session, actor_id=principal.user_id, escrow_id=escrow_id)
    response.headers["Cache-Control"] = "no-store"
    return _escrow_response(item)


@router.post(
    "/escrows/{escrow_id}/fund",
    response_model=EscrowResponse,
    summary="Fund escrow with simulated virtual credits",
    description="Moves VCR virtual credits into escrow. These credits have no real-world value.",
    responses=ERROR_RESPONSES,
)
async def fund_my_escrow(
    escrow_id: UUID,
    payload: EscrowActionRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> EscrowResponse:
    item = await fund_escrow(
        session,
        actor_id=principal.user_id,
        escrow_id=escrow_id,
        idempotency_key=payload.idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return _escrow_response(item)


@router.post(
    "/escrows/{escrow_id}/release",
    response_model=EscrowResponse,
    summary="Release simulated escrow credits to agent owner",
    description=(
        "Demo-only lifecycle action. This is not proof of work verification or a real payment."
    ),
    responses=ERROR_RESPONSES,
)
async def release_my_escrow(
    escrow_id: UUID,
    payload: EscrowActionRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> EscrowResponse:
    item = await release_escrow(
        session,
        actor_id=principal.user_id,
        escrow_id=escrow_id,
        idempotency_key=payload.idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return _escrow_response(item)


@router.post(
    "/escrows/{escrow_id}/refund",
    response_model=EscrowResponse,
    summary="Refund simulated escrow credits to buyer",
    description="Demo-only lifecycle action. No external funds are returned.",
    responses=ERROR_RESPONSES,
)
async def refund_my_escrow(
    escrow_id: UUID,
    payload: EscrowActionRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> EscrowResponse:
    item = await refund_escrow(
        session,
        actor_id=principal.user_id,
        escrow_id=escrow_id,
        idempotency_key=payload.idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return _escrow_response(item)


@router.get(
    "/ledger/me",
    response_model=LedgerBalanceResponse,
    summary="Get simulated virtual-credit balances",
    description="All balances are VCR demo units with no monetary value.",
)
async def my_ledger_balance(
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
) -> LedgerBalanceResponse:
    available, locked = await get_balances(session, actor_id=principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    return LedgerBalanceResponse(available=available, escrow_locked=locked)


@router.get(
    "/ledger/me/transactions",
    response_model=LedgerHistoryResponse,
    summary="List simulated ledger transactions",
)
async def my_ledger_history(
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LedgerHistoryResponse:
    items, total = await ledger_history(
        session, actor_id=principal.user_id, limit=limit, offset=offset
    )
    response.headers["Cache-Control"] = "no-store"
    return LedgerHistoryResponse(
        items=[LedgerTransactionResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/admin/demo-credits/{target_user_id}",
    response_model=LedgerTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue admin-controlled simulated demo credits",
    description=(
        "Only available when demo-credit provisioning is explicitly enabled outside production. "
        "Credits have no real value."
    ),
    responses=ERROR_RESPONSES,
)
async def issue_demo_credits(
    target_user_id: UUID,
    payload: DemoCreditRequest,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.DEMO_CREDIT_ISSUE))],
) -> LedgerTransactionResponse:
    if settings.environment == "production" or not settings.demo_credit_enabled:
        raise ApiError(404, "not_found", "This operation is not available.")
    if payload.amount > 10000:
        raise ApiError(
            422, "demo_credit_limit", "A single demo-credit issuance cannot exceed 10,000 VCR."
        )
    item = await provision_demo_credits(
        session,
        actor_id=principal.user_id,
        target_user_id=target_user_id,
        amount=payload.amount,
        idempotency_key=payload.idempotency_key,
    )
    response.headers["Cache-Control"] = "no-store"
    return LedgerTransactionResponse.model_validate(item)
