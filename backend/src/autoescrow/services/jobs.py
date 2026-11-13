from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import asc, desc, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.models.domain import Job
from autoescrow.models.enums import JobStatus, JobVisibility
from autoescrow.models.identity import User
from autoescrow.schemas.jobs import JobCreateRequest, JobUpdateRequest
from autoescrow.services.audit import AuditEventType, record_audit

JobSort = Literal["created_at", "deadline", "budget_amount", "updated_at"]
SortOrder = Literal["asc", "desc"]
PublicJob = tuple[Job, str]

_EDITABLE_STATUSES = frozenset({JobStatus.DRAFT.value, JobStatus.OPEN.value})
_CANCELLABLE_STATUSES = frozenset(
    {JobStatus.DRAFT.value, JobStatus.OPEN.value, JobStatus.IN_PROGRESS.value}
)
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.DRAFT.value: frozenset({JobStatus.DRAFT.value, JobStatus.OPEN.value}),
    JobStatus.OPEN.value: frozenset(
        {JobStatus.OPEN.value, JobStatus.DRAFT.value, JobStatus.CANCELLED.value}
    ),
    JobStatus.IN_PROGRESS.value: frozenset(
        {JobStatus.IN_PROGRESS.value, JobStatus.CANCELLED.value}
    ),
    JobStatus.CANCELLED.value: frozenset({JobStatus.CANCELLED.value}),
    JobStatus.COMPLETED.value: frozenset({JobStatus.COMPLETED.value}),
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_deadline(deadline: datetime, *, now: datetime | None = None) -> None:
    resolved_now = now or datetime.now(UTC)
    if deadline.tzinfo is None:
        raise ApiError(422, "invalid_deadline", "Deadline must include a timezone.")
    if deadline <= resolved_now:
        raise ApiError(422, "invalid_deadline", "Deadline must be in the future.")


async def _owned_job(
    session: AsyncSession,
    *,
    owner_id: UUID,
    job_id: UUID,
    for_update: bool = False,
) -> Job:
    statement = select(Job).where(Job.id == job_id, Job.owner_user_id == owner_id)
    if for_update:
        statement = statement.with_for_update()
    job = await session.scalar(statement)
    if job is None:
        raise ApiError(404, "job_not_found", "Job not found.")
    return job


async def create_job(
    session: AsyncSession,
    *,
    owner: User,
    payload: JobCreateRequest,
) -> Job:
    _validate_deadline(payload.deadline)
    job = Job(
        owner_user_id=owner.id,
        title=payload.title,
        description=payload.description,
        category=payload.category.value,
        required_skills=payload.required_skills,
        required_capabilities=payload.required_capabilities,
        budget_amount=payload.budget_amount,
        currency=payload.currency,
        deadline=payload.deadline,
        expected_deliverables=payload.expected_deliverables,
        status=payload.status.value,
        visibility=payload.visibility.value,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "job_conflict", "The job could not be created.") from exc

    await record_audit(
        session,
        event_type=AuditEventType.JOB_CREATED,
        subject_type="job",
        subject_id=job.id,
        actor_user_id=owner.id,
        metadata={
            "status": job.status,
            "visibility": job.visibility,
            "category": job.category,
        },
    )
    await session.commit()
    return job


async def list_owned_jobs(
    session: AsyncSession,
    *,
    owner_id: UUID,
    status: JobStatus | None,
    visibility: JobVisibility | None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    conditions: list[Any] = [Job.owner_user_id == owner_id]
    if status is not None:
        conditions.append(Job.status == status.value)
    if visibility is not None:
        conditions.append(Job.visibility == visibility.value)

    total = int(await session.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0)
    jobs = list(
        await session.scalars(
            select(Job)
            .where(*conditions)
            .order_by(Job.created_at.desc(), Job.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return jobs, total


async def get_owned_job(
    session: AsyncSession,
    *,
    owner_id: UUID,
    job_id: UUID,
) -> Job:
    return await _owned_job(session, owner_id=owner_id, job_id=job_id)


async def update_job(
    session: AsyncSession,
    *,
    owner_id: UUID,
    job_id: UUID,
    payload: JobUpdateRequest,
) -> Job:
    job = await _owned_job(session, owner_id=owner_id, job_id=job_id, for_update=True)
    if job.status not in _EDITABLE_STATUSES:
        raise ApiError(409, "job_not_editable", "This job can no longer be edited.")

    updates = payload.model_dump(exclude_unset=True)
    requested_status = updates.pop("status", None)

    if requested_status is not None and requested_status != job.status:
        normalized_status = JobStatus(requested_status).value
        if normalized_status == JobStatus.CANCELLED.value:
            raise ApiError(
                409,
                "invalid_job_transition",
                "Use the cancel endpoint to cancel a job.",
            )
        if normalized_status not in _ALLOWED_TRANSITIONS[job.status]:
            raise ApiError(
                409,
                "invalid_job_transition",
                f"Job cannot transition from {job.status} to {normalized_status}.",
            )
        job.status = normalized_status

    if "deadline" in updates and updates["deadline"] is not None:
        _validate_deadline(updates["deadline"])

    for field, value in updates.items():
        if field in {"category", "visibility"} and value is not None:
            value = str(value)
        setattr(job, field, value)

    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.JOB_UPDATED,
        subject_type="job",
        subject_id=job.id,
        actor_user_id=owner_id,
        metadata={"status": job.status, "fields": sorted(updates)},
    )
    await session.commit()
    return job


async def cancel_job(
    session: AsyncSession,
    *,
    owner_id: UUID,
    job_id: UUID,
) -> Job:
    job = await _owned_job(session, owner_id=owner_id, job_id=job_id, for_update=True)
    if job.status == JobStatus.CANCELLED.value:
        return job
    if job.status not in _CANCELLABLE_STATUSES:
        raise ApiError(409, "job_not_cancellable", "This job can no longer be cancelled.")

    job.status = JobStatus.CANCELLED.value
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.JOB_CANCELLED,
        subject_type="job",
        subject_id=job.id,
        actor_user_id=owner_id,
        metadata={"status": job.status},
    )
    await session.commit()
    return job


async def list_public_jobs(
    session: AsyncSession,
    *,
    search: str | None,
    category: str | None,
    sort: JobSort,
    order: SortOrder,
    limit: int,
    offset: int,
) -> tuple[list[PublicJob], int]:
    conditions: list[Any] = [
        Job.status == JobStatus.OPEN.value,
        Job.visibility == JobVisibility.PUBLIC.value,
    ]
    if category is not None:
        conditions.append(Job.category == category)
    if search:
        pattern = f"%{_escape_like(search.strip())}%"
        conditions.append(
            or_(Job.title.ilike(pattern, escape="\\"), Job.description.ilike(pattern, escape="\\"))
        )

    total = int(await session.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0)
    sort_columns = {
        "created_at": Job.created_at,
        "deadline": Job.deadline,
        "budget_amount": Job.budget_amount,
        "updated_at": Job.updated_at,
    }
    ordering = asc if order == "asc" else desc
    statement = (
        select(Job, User.display_name)
        .join(User, User.id == Job.owner_user_id)
        .where(*conditions)
        .order_by(ordering(sort_columns[sort]), Job.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = await session.execute(statement)
    return [(row[0], row[1]) for row in rows.all()], total


async def get_job_for_viewer(
    session: AsyncSession,
    *,
    job_id: UUID,
    viewer_id: UUID | None,
) -> PublicJob | None:
    visibility_condition = (Job.owner_user_id == viewer_id) if viewer_id is not None else false()
    row = (
        await session.execute(
            select(Job, User.display_name)
            .join(User, User.id == Job.owner_user_id)
            .where(
                Job.id == job_id,
                or_(
                    visibility_condition,
                    (Job.status == JobStatus.OPEN.value)
                    & (Job.visibility == JobVisibility.PUBLIC.value),
                ),
            )
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]
