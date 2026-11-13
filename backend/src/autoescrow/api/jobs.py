from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from autoescrow.api.dependencies import (
    CurrentUser,
    DbSession,
    OptionalUser,
    require_permission,
)
from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission, has_permission
from autoescrow.models.domain import Job
from autoescrow.models.enums import JobStatus, JobVisibility, TaskCategory
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.jobs import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobUpdateRequest,
)
from autoescrow.services.jobs import (
    JobSort,
    SortOrder,
    cancel_job,
    create_job,
    get_job_for_viewer,
    list_owned_jobs,
    list_public_jobs,
    update_job,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _response(job: Job, owner_display_name: str) -> JobResponse:
    return JobResponse.from_job(job, owner_display_name=owner_display_name)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=JobResponse,
    summary="Create a job",
    responses=ERROR_RESPONSES,
)
async def create_my_job(
    payload: JobCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.JOB_WRITE)),
    ],
) -> JobResponse:
    job = await create_job(session, owner=principal.user, payload=payload)
    response.headers["Cache-Control"] = "no-store"
    return _response(job, principal.user.display_name)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List the authenticated user's jobs",
    responses=ERROR_RESPONSES,
)
async def list_my_jobs(
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.JOB_READ)),
    ],
    status: JobStatus | None = None,
    visibility: JobVisibility | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    jobs, total = await list_owned_jobs(
        session,
        owner_id=principal.user_id,
        status=status,
        visibility=visibility,
        limit=limit,
        offset=offset,
    )
    response.headers["Cache-Control"] = "no-store"
    return JobListResponse(
        items=[
            JobResponse.from_job(job, owner_display_name=principal.user.display_name)
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/public",
    response_model=JobListResponse,
    summary="Discover public open jobs",
    responses=ERROR_RESPONSES,
)
async def list_public_open_jobs(
    session: DbSession,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category: TaskCategory | None = None,
    sort: JobSort = "created_at",
    order: SortOrder = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    jobs, total = await list_public_jobs(
        session,
        search=search,
        category=category.value if category else None,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(
        items=[
            JobResponse.from_job(job, owner_display_name=owner_display_name)
            for job, owner_display_name in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Read a job visible to the caller",
    responses=ERROR_RESPONSES,
)
async def get_job(
    job_id: UUID,
    session: DbSession,
    principal: OptionalUser,
) -> JobResponse:
    if principal is not None and not has_permission(principal.roles, Permission.JOB_READ):
        raise ApiError(403, "forbidden", "You do not have permission to perform this action.")

    result = await get_job_for_viewer(
        session,
        job_id=job_id,
        viewer_id=principal.user_id if principal is not None else None,
    )
    if result is None:
        raise ApiError(404, "job_not_found", "Job not found.")
    job, owner_display_name = result
    return JobResponse.from_job(job, owner_display_name=owner_display_name)


@router.patch(
    "/{job_id}",
    response_model=JobResponse,
    summary="Update one of the authenticated user's jobs",
    responses=ERROR_RESPONSES,
)
async def update_my_job(
    job_id: UUID,
    payload: JobUpdateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.JOB_WRITE)),
    ],
) -> JobResponse:
    job = await update_job(
        session,
        owner_id=principal.user_id,
        job_id=job_id,
        payload=payload,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(job, principal.user.display_name)


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    summary="Cancel one of the authenticated user's jobs",
    responses=ERROR_RESPONSES,
)
async def cancel_my_job(
    job_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.JOB_WRITE)),
    ],
) -> JobResponse:
    job = await cancel_job(session, owner_id=principal.user_id, job_id=job_id)
    response.headers["Cache-Control"] = "no-store"
    return _response(job, principal.user.display_name)
