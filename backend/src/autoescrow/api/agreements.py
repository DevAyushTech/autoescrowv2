from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from autoescrow.api.dependencies import CurrentUser, DbSession, require_permission
from autoescrow.core.permissions import Permission
from autoescrow.schemas.agreements import (
    AgreementAcceptanceResponse,
    AgreementAcceptRequest,
    AgreementCreateRequest,
    AgreementListResponse,
    AgreementResponse,
    AgreementVersionCreateRequest,
    AgreementVersionResponse,
)
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.services.agreements import (
    AgreementView,
    accept_version,
    create_agreement,
    create_version,
    get_agreement,
    list_agreements,
)

router = APIRouter(prefix="/agreements", tags=["agreements"])


def _response(view: AgreementView) -> AgreementResponse:
    version_numbers = {version.id: version.version_number for version in view.versions}
    versions = [AgreementVersionResponse.from_version(version) for version in view.versions]
    current = next(
        (
            version
            for version in versions
            if version.version_number == view.agreement.current_version_number
        ),
        None,
    )
    if current is None:
        raise RuntimeError("agreement current version is missing")
    return AgreementResponse(
        id=view.agreement.id,
        job_id=view.agreement.job_id,
        agent_id=view.agreement.agent_id,
        owner_user_id=view.agreement.owner_user_id,
        agent_owner_user_id=view.agreement.agent_owner_user_id,
        status=view.agreement.status,
        current_version_number=view.agreement.current_version_number,
        current_version=current,
        versions=versions,
        acceptances=[
            AgreementAcceptanceResponse.from_acceptance(
                acceptance,
                version_number=version_numbers[acceptance.agreement_version_id],
            )
            for acceptance in view.acceptances
        ],
        created_at=view.agreement.created_at,
        updated_at=view.agreement.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AgreementResponse,
    summary="Create a versioned agreement with a selected agent",
    responses=ERROR_RESPONSES,
)
async def create_new_agreement(
    payload: AgreementCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGREEMENT_WRITE)),
    ],
) -> AgreementResponse:
    view = await create_agreement(
        session,
        actor_id=principal.user_id,
        job_id=payload.job_id,
        agent_id=payload.agent_id,
        terms=payload.terms,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(view)


@router.get(
    "",
    response_model=AgreementListResponse,
    summary="List agreements involving the authenticated user",
    responses=ERROR_RESPONSES,
)
async def list_my_agreements(
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGREEMENT_READ)),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgreementListResponse:
    views, total = await list_agreements(
        session,
        actor_id=principal.user_id,
        limit=limit,
        offset=offset,
    )
    response.headers["Cache-Control"] = "no-store"
    return AgreementListResponse(
        items=[_response(view) for view in views],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{agreement_id}",
    response_model=AgreementResponse,
    summary="Read an agreement as a participant",
    responses=ERROR_RESPONSES,
)
async def get_my_agreement(
    agreement_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGREEMENT_READ)),
    ],
) -> AgreementResponse:
    view = await get_agreement(
        session,
        actor_id=principal.user_id,
        agreement_id=agreement_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(view)


@router.post(
    "/{agreement_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=AgreementResponse,
    summary="Create a new immutable agreement version",
    responses=ERROR_RESPONSES,
)
async def create_agreement_version(
    agreement_id: UUID,
    payload: AgreementVersionCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGREEMENT_WRITE)),
    ],
) -> AgreementResponse:
    view = await create_version(
        session,
        actor_id=principal.user_id,
        agreement_id=agreement_id,
        expected_version_number=payload.expected_version_number,
        terms=payload.terms,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(view)


@router.post(
    "/{agreement_id}/accept",
    response_model=AgreementResponse,
    summary="Accept the current agreement version",
    responses=ERROR_RESPONSES,
)
async def accept_agreement_version(
    agreement_id: UUID,
    payload: AgreementAcceptRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGREEMENT_WRITE)),
    ],
) -> AgreementResponse:
    view = await accept_version(
        session,
        actor_id=principal.user_id,
        agreement_id=agreement_id,
        version_number=payload.version_number,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(view)
