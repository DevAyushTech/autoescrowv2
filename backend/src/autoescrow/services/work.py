from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.models.domain import Agent, Agreement, Job
from autoescrow.models.ledger import Escrow
from autoescrow.models.work import WorkDispute, WorkSubmission
from autoescrow.services.audit import AuditEventType, record_audit


async def _participant_escrow(
    session: AsyncSession, escrow_id: UUID, actor_id: UUID, *, lock: bool = False
) -> Escrow:
    stmt = select(Escrow).where(
        Escrow.id == escrow_id,
        or_(Escrow.buyer_user_id == actor_id, Escrow.agent_owner_user_id == actor_id),
    )
    if lock:
        stmt = stmt.with_for_update()
    escrow = await session.scalar(stmt)
    if escrow is None:
        raise ApiError(404, "escrow_not_found", "Escrow not found.")
    return escrow


async def list_submissions(
    session: AsyncSession, *, escrow_id: UUID, actor_id: UUID
) -> list[WorkSubmission]:
    await _participant_escrow(session, escrow_id, actor_id)
    return list(
        await session.scalars(
            select(WorkSubmission)
            .where(WorkSubmission.escrow_id == escrow_id)
            .order_by(WorkSubmission.attempt.desc())
        )
    )


async def create_submission(
    session: AsyncSession,
    *,
    escrow_id: UUID,
    actor_id: UUID,
    summary: str,
    deliverables: list[dict[str, str]],
    executor: str = "manual",
) -> WorkSubmission:
    escrow = await _participant_escrow(session, escrow_id, actor_id, lock=True)
    if actor_id != escrow.agent_owner_user_id:
        raise ApiError(403, "agent_owner_only", "Only the contracted agent owner can submit work.")
    if escrow.status != "funded":
        raise ApiError(
            409, "escrow_not_funded", "Work can only be submitted against a funded escrow."
        )
    if await session.scalar(
        select(WorkDispute.id).where(
            WorkDispute.escrow_id == escrow.id, WorkDispute.status == "open"
        )
    ):
        raise ApiError(409, "dispute_open", "Work cannot be resubmitted while a dispute is open.")
    latest = await session.scalar(
        select(func.max(WorkSubmission.attempt)).where(WorkSubmission.escrow_id == escrow.id)
    )
    submission = WorkSubmission(
        escrow_id=escrow.id,
        job_id=(
            await session.scalar(
                select(Agreement.job_id).where(Agreement.id == escrow.agreement_id)
            )
        ),
        submitted_by_user_id=actor_id,
        attempt=int(latest or 0) + 1,
        status="submitted",
        summary=summary,
        deliverables=deliverables,
        executor=executor,
    )
    session.add(submission)
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.WORK_SUBMITTED,
        subject_type="work_submission",
        subject_id=submission.id,
        actor_user_id=actor_id,
        metadata={"escrow_id": str(escrow.id), "attempt": submission.attempt, "executor": executor},
    )
    await session.commit()
    return submission


async def run_demo_executor(
    session: AsyncSession, *, escrow_id: UUID, actor_id: UUID
) -> WorkSubmission:
    escrow = await _participant_escrow(session, escrow_id, actor_id)
    if actor_id != escrow.agent_owner_user_id:
        raise ApiError(
            403, "agent_owner_only", "Only the contracted agent owner can run this demo executor."
        )
    agreement = await session.get(Agreement, escrow.agreement_id)
    if agreement is None:
        raise ApiError(404, "agreement_not_found", "Agreement not found.")
    job = await session.get(Job, agreement.job_id)
    agent = await session.get(Agent, agreement.agent_id)
    if job is None or agent is None:
        raise ApiError(
            409, "execution_context_missing", "Job or agent configuration is unavailable."
        )
    # This is an explicit deterministic demo adapter, not a claim that an external AI agent ran.
    summary = (
        f"Demo execution completed for '{job.title}' by '{agent.name}'. "
        "The executor prepared a structured sample result for reviewer acceptance. "
        "No external model, browser action, or third-party tool was invoked."
    )
    deliverables = [
        {
            "name": item,
            "status": "demo_sample",
            "description": f"Sample output prepared for: {item}",
        }
        for item in job.expected_deliverables
    ]
    return await create_submission(
        session,
        escrow_id=escrow_id,
        actor_id=actor_id,
        summary=summary,
        deliverables=deliverables,
        executor="deterministic_demo",
    )


async def review_submission(
    session: AsyncSession,
    *,
    submission_id: UUID,
    actor_id: UUID,
    decision: str,
    note: str,
) -> WorkSubmission:
    submission = await session.scalar(
        select(WorkSubmission).where(WorkSubmission.id == submission_id).with_for_update()
    )
    if submission is None:
        raise ApiError(404, "submission_not_found", "Submission not found.")
    escrow = await _participant_escrow(session, submission.escrow_id, actor_id, lock=True)
    if actor_id != escrow.buyer_user_id:
        raise ApiError(403, "buyer_only", "Only the job owner can review submitted work.")
    if escrow.status != "funded" or submission.status != "submitted":
        raise ApiError(409, "submission_not_reviewable", "This submission is no longer reviewable.")
    submission.status = "approved" if decision == "approve" else "rejected"
    submission.review_note = note
    submission.reviewed_by_user_id = actor_id
    submission.reviewed_at = datetime.now(UTC)
    await record_audit(
        session,
        event_type=AuditEventType.WORK_APPROVED
        if decision == "approve"
        else AuditEventType.WORK_REJECTED,
        subject_type="work_submission",
        subject_id=submission.id,
        actor_user_id=actor_id,
        metadata={"escrow_id": str(escrow.id), "decision": decision},
    )
    await session.commit()
    return submission


async def open_dispute(
    session: AsyncSession,
    *,
    submission_id: UUID,
    actor_id: UUID,
    reason: str,
) -> WorkDispute:
    submission = await session.scalar(
        select(WorkSubmission).where(WorkSubmission.id == submission_id).with_for_update()
    )
    if submission is None:
        raise ApiError(404, "submission_not_found", "Submission not found.")
    escrow = await _participant_escrow(session, submission.escrow_id, actor_id, lock=True)
    if escrow.status != "funded" or submission.status not in {"submitted", "approved", "rejected"}:
        raise ApiError(
            409, "dispute_not_allowed", "A dispute can only be opened before escrow settlement."
        )
    existing = await session.scalar(
        select(WorkDispute).where(WorkDispute.submission_id == submission.id)
    )
    if existing is not None:
        raise ApiError(409, "dispute_exists", "A dispute already exists for this submission.")
    dispute = WorkDispute(
        submission_id=submission.id,
        escrow_id=escrow.id,
        raised_by_user_id=actor_id,
        reason=reason,
        status="open",
    )
    submission.status = "disputed"
    session.add(dispute)
    await session.flush()
    await record_audit(
        session,
        event_type=AuditEventType.DISPUTE_OPENED,
        subject_type="work_dispute",
        subject_id=dispute.id,
        actor_user_id=actor_id,
        metadata={"escrow_id": str(escrow.id), "submission_id": str(submission.id)},
    )
    await session.commit()
    return dispute


async def resolve_dispute(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    actor_id: UUID,
    resolution: str,
    note: str,
    roles: frozenset[str],
) -> WorkDispute:
    if not roles.intersection({"ADMIN", "ARBITRATOR"}):
        raise ApiError(403, "arbitrator_only", "Only an admin or arbitrator can resolve disputes.")
    dispute = await session.scalar(
        select(WorkDispute).where(WorkDispute.id == dispute_id).with_for_update()
    )
    if dispute is None:
        raise ApiError(404, "dispute_not_found", "Dispute not found.")
    if dispute.status != "open":
        raise ApiError(409, "dispute_resolved", "This dispute has already been resolved.")
    dispute.status = "resolved"
    dispute.resolution = resolution
    dispute.resolution_note = note
    dispute.resolved_by_user_id = actor_id
    dispute.resolved_at = datetime.now(UTC)
    submission = await session.get(WorkSubmission, dispute.submission_id)
    if submission is not None:
        submission.status = "approved" if resolution == "release" else "rejected"
        submission.review_note = f"Dispute resolution: {note}"
        submission.reviewed_by_user_id = actor_id
        submission.reviewed_at = datetime.now(UTC)
    await record_audit(
        session,
        event_type=AuditEventType.DISPUTE_RESOLVED,
        subject_type="work_dispute",
        subject_id=dispute.id,
        actor_user_id=actor_id,
        metadata={"resolution": resolution, "escrow_id": str(dispute.escrow_id)},
    )
    await session.commit()
    return dispute


async def list_disputes(
    session: AsyncSession,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[WorkDispute], int]:
    filters = [WorkDispute.status == status] if status else []
    total = int(
        await session.scalar(select(func.count()).select_from(WorkDispute).where(*filters)) or 0
    )
    items = list(
        await session.scalars(
            select(WorkDispute)
            .where(*filters)
            .order_by(WorkDispute.created_at.desc(), WorkDispute.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total
