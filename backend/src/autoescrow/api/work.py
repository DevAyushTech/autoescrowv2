from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from autoescrow.api.dependencies import CurrentUser, DbSession, require_permission
from autoescrow.core.permissions import Permission
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.work import (
    DisputeCreateRequest,
    DisputeResolveRequest,
    DisputeResponse,
    SubmissionCreateRequest,
    SubmissionResponse,
    SubmissionReviewRequest,
)
from autoescrow.services.work import (
    create_submission,
    list_submissions,
    open_dispute,
    resolve_dispute,
    review_submission,
    run_demo_executor,
)

router = APIRouter(tags=["work-execution-and-disputes"])


@router.get("/escrows/{escrow_id}/submissions", response_model=list[SubmissionResponse])
async def get_submissions(
    escrow_id: UUID,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_READ))],
) -> list[SubmissionResponse]:
    return [
        SubmissionResponse.model_validate(item)
        for item in await list_submissions(session, escrow_id=escrow_id, actor_id=principal.user_id)
    ]


@router.post(
    "/escrows/{escrow_id}/submissions",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def submit_work(
    escrow_id: UUID,
    payload: SubmissionCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> SubmissionResponse:
    item = await create_submission(
        session,
        escrow_id=escrow_id,
        actor_id=principal.user_id,
        summary=payload.summary,
        deliverables=payload.deliverables,
    )
    response.headers["Cache-Control"] = "no-store"
    return SubmissionResponse.model_validate(item)


@router.post(
    "/escrows/{escrow_id}/demo-execute",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def execute_demo_agent(
    escrow_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> SubmissionResponse:
    item = await run_demo_executor(session, escrow_id=escrow_id, actor_id=principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    return SubmissionResponse.model_validate(item)


@router.post(
    "/submissions/{submission_id}/review",
    response_model=SubmissionResponse,
    responses=ERROR_RESPONSES,
)
async def review_work(
    submission_id: UUID,
    payload: SubmissionReviewRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> SubmissionResponse:
    item = await review_submission(
        session,
        submission_id=submission_id,
        actor_id=principal.user_id,
        decision=payload.decision,
        note=payload.note,
    )
    response.headers["Cache-Control"] = "no-store"
    return SubmissionResponse.model_validate(item)


@router.post(
    "/submissions/{submission_id}/disputes",
    response_model=DisputeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def raise_dispute(
    submission_id: UUID,
    payload: DisputeCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AGREEMENT_WRITE))],
) -> DisputeResponse:
    item = await open_dispute(
        session, submission_id=submission_id, actor_id=principal.user_id, reason=payload.reason
    )
    response.headers["Cache-Control"] = "no-store"
    return DisputeResponse.model_validate(item)


@router.post(
    "/disputes/{dispute_id}/resolve", response_model=DisputeResponse, responses=ERROR_RESPONSES
)
async def resolve_work_dispute(
    dispute_id: UUID,
    payload: DisputeResolveRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[CurrentUser, Depends(require_permission(Permission.AUDIT_READ))],
) -> DisputeResponse:
    item = await resolve_dispute(
        session,
        dispute_id=dispute_id,
        actor_id=principal.user_id,
        resolution=payload.resolution,
        note=payload.note,
        roles=principal.roles,
    )
    response.headers["Cache-Control"] = "no-store"
    return DisputeResponse.model_validate(item)
