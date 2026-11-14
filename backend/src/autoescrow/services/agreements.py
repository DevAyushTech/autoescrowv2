import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.models.domain import (
    Agent,
    Agreement,
    AgreementAcceptance,
    AgreementVersion,
    Job,
)
from autoescrow.models.enums import (
    AgentStatus,
    AgreementParty,
    AgreementStatus,
    JobStatus,
)
from autoescrow.schemas.agreements import AgreementTerms
from autoescrow.services.audit import AuditEventType, record_audit


@dataclass(frozen=True, slots=True)
class AgreementView:
    agreement: Agreement
    versions: list[AgreementVersion]
    acceptances: list[AgreementAcceptance]


def canonical_terms(terms: AgreementTerms) -> dict[str, Any]:
    """Return a stable, JSON-safe representation of agreement terms."""

    deadline = terms.deadline.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "scope": terms.scope,
        "deliverables": sorted(terms.deliverables),
        "budget_amount": str(terms.budget_amount.quantize(Decimal("0.01"))),
        "currency": terms.currency,
        "deadline": deadline,
        "responsibilities": sorted(terms.responsibilities),
        "acceptance_conditions": sorted(terms.acceptance_conditions),
    }


def hash_terms(terms: dict[str, Any]) -> str:
    encoded = json.dumps(
        terms,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_terms(
    terms: AgreementTerms,
    *,
    job: Job,
    now: datetime | None = None,
) -> None:
    resolved_now = now or datetime.now(UTC)
    if terms.deadline <= resolved_now:
        raise ApiError(422, "invalid_deadline", "Agreement deadline must be in the future.")
    if terms.currency != job.currency:
        raise ApiError(
            422,
            "currency_mismatch",
            "Agreement currency must match the job currency.",
        )
    if terms.budget_amount > job.budget_amount:
        raise ApiError(
            422,
            "budget_exceeds_job",
            "Agreement budget cannot exceed the job budget.",
        )
    if terms.deadline > job.deadline:
        raise ApiError(
            422,
            "deadline_exceeds_job",
            "Agreement deadline cannot exceed the job deadline.",
        )


def _ensure_job_allows_agreement_activity(job: Job) -> None:
    if job.status not in {JobStatus.OPEN.value, JobStatus.IN_PROGRESS.value}:
        raise ApiError(
            409,
            "job_not_open",
            "The job is not open for agreement activity.",
        )


async def _get_participant_agreement(
    session: AsyncSession,
    *,
    agreement_id: UUID,
    actor_id: UUID,
    for_update: bool = False,
) -> Agreement:
    statement = select(Agreement).where(
        Agreement.id == agreement_id,
        or_(
            Agreement.owner_user_id == actor_id,
            Agreement.agent_owner_user_id == actor_id,
        ),
    )
    if for_update:
        statement = statement.with_for_update()
    agreement = await session.scalar(statement)
    if agreement is None:
        raise ApiError(404, "agreement_not_found", "Agreement not found.")
    return agreement


def _party_for(agreement: Agreement, actor_id: UUID) -> AgreementParty:
    if actor_id == agreement.owner_user_id:
        return AgreementParty.OWNER
    if actor_id == agreement.agent_owner_user_id:
        return AgreementParty.AGENT_OWNER
    raise ApiError(404, "agreement_not_found", "Agreement not found.")


async def _load_view(session: AsyncSession, agreement: Agreement) -> AgreementView:
    versions = list(
        await session.scalars(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement.id)
            .order_by(AgreementVersion.version_number.asc())
        )
    )
    version_ids = [version.id for version in versions]
    acceptances: list[AgreementAcceptance] = []
    if version_ids:
        acceptances = list(
            await session.scalars(
                select(AgreementAcceptance)
                .where(AgreementAcceptance.agreement_version_id.in_(version_ids))
                .order_by(AgreementAcceptance.accepted_at.asc())
            )
        )
    return AgreementView(agreement=agreement, versions=versions, acceptances=acceptances)


async def create_agreement(
    session: AsyncSession,
    *,
    actor_id: UUID,
    job_id: UUID,
    agent_id: UUID,
    terms: AgreementTerms,
) -> AgreementView:
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.owner_user_id != actor_id:
        raise ApiError(404, "job_not_found", "Job not found.")
    if job.status != JobStatus.OPEN.value:
        raise ApiError(409, "job_not_open", "Agreements require an open job.")

    agent = await session.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None or agent.status != AgentStatus.ACTIVE.value or not agent.is_public:
        raise ApiError(404, "agent_not_found", "Agent not found.")
    if agent.owner_user_id == actor_id:
        raise ApiError(409, "self_agreement", "A job owner cannot contract their own agent.")

    _validate_terms(terms, job=job)

    existing = await session.scalar(
        select(Agreement).where(Agreement.job_id == job_id, Agreement.agent_id == agent_id)
    )
    if existing is not None:
        raise ApiError(409, "agreement_exists", "An agreement already exists for this agent.")

    canonical = canonical_terms(terms)
    agreement = Agreement(
        job_id=job.id,
        agent_id=agent.id,
        owner_user_id=job.owner_user_id,
        agent_owner_user_id=agent.owner_user_id,
        created_by_user_id=actor_id,
        status=AgreementStatus.PENDING_ACCEPTANCE.value,
        current_version_number=1,
    )
    session.add(agreement)
    try:
        await session.flush()
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            terms=canonical,
            terms_hash=hash_terms(canonical),
            created_by_user_id=actor_id,
        )
        session.add(version)
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "agreement_exists",
            "An agreement already exists for this agent.",
        ) from exc

    await record_audit(
        session,
        event_type=AuditEventType.AGREEMENT_CREATED,
        subject_type="agreement",
        subject_id=agreement.id,
        actor_user_id=actor_id,
        metadata={"job_id": str(job.id), "agent_id": str(agent.id), "version": 1},
    )
    await record_audit(
        session,
        event_type=AuditEventType.AGREEMENT_VERSION_CREATED,
        subject_type="agreement_version",
        subject_id=version.id,
        actor_user_id=actor_id,
        metadata={"agreement_id": str(agreement.id), "version": 1},
    )
    await session.commit()
    return AgreementView(agreement=agreement, versions=[version], acceptances=[])


async def create_version(
    session: AsyncSession,
    *,
    actor_id: UUID,
    agreement_id: UUID,
    expected_version_number: int,
    terms: AgreementTerms,
) -> AgreementView:
    agreement = await _get_participant_agreement(
        session,
        agreement_id=agreement_id,
        actor_id=actor_id,
        for_update=True,
    )
    if agreement.status == AgreementStatus.CANCELLED.value:
        raise ApiError(409, "agreement_cancelled", "A cancelled agreement cannot change.")
    if agreement.current_version_number != expected_version_number:
        raise ApiError(
            409,
            "stale_agreement_version",
            "The agreement has a newer version.",
        )

    job = await session.scalar(select(Job).where(Job.id == agreement.job_id))
    if job is None:
        raise RuntimeError("agreement references a missing job")
    _ensure_job_allows_agreement_activity(job)
    _validate_terms(terms, job=job)

    next_version = agreement.current_version_number + 1
    canonical = canonical_terms(terms)
    version = AgreementVersion(
        agreement_id=agreement.id,
        version_number=next_version,
        terms=canonical,
        terms_hash=hash_terms(canonical),
        created_by_user_id=actor_id,
    )
    agreement.current_version_number = next_version
    agreement.status = AgreementStatus.PENDING_ACCEPTANCE.value
    agreement.activated_at = None
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "stale_agreement_version",
            "The agreement has a newer version.",
        ) from exc

    await record_audit(
        session,
        event_type=AuditEventType.AGREEMENT_VERSION_CREATED,
        subject_type="agreement_version",
        subject_id=version.id,
        actor_user_id=actor_id,
        metadata={"agreement_id": str(agreement.id), "version": next_version},
    )
    await session.commit()
    return await _load_view(session, agreement)


async def accept_version(
    session: AsyncSession,
    *,
    actor_id: UUID,
    agreement_id: UUID,
    version_number: int,
) -> AgreementView:
    agreement = await _get_participant_agreement(
        session,
        agreement_id=agreement_id,
        actor_id=actor_id,
        for_update=True,
    )
    if agreement.status == AgreementStatus.CANCELLED.value:
        raise ApiError(409, "agreement_cancelled", "A cancelled agreement cannot be accepted.")
    if version_number != agreement.current_version_number:
        raise ApiError(
            409,
            "outdated_agreement_version",
            "Only the current agreement version can be accepted.",
        )

    version = await session.scalar(
        select(AgreementVersion).where(
            AgreementVersion.agreement_id == agreement.id,
            AgreementVersion.version_number == version_number,
        )
    )
    if version is None:
        raise ApiError(409, "outdated_agreement_version", "Agreement version not found.")

    job = await session.scalar(select(Job).where(Job.id == agreement.job_id).with_for_update())
    if job is None:
        raise RuntimeError("agreement references a missing job")
    _ensure_job_allows_agreement_activity(job)
    _validate_terms(AgreementTerms.model_validate(version.terms), job=job)

    party = _party_for(agreement, actor_id)
    existing = await session.scalar(
        select(AgreementAcceptance).where(
            AgreementAcceptance.agreement_version_id == version.id,
            AgreementAcceptance.user_id == actor_id,
        )
    )
    if existing is not None:
        raise ApiError(409, "already_accepted", "You already accepted this version.")

    acceptance = AgreementAcceptance(
        agreement_version_id=version.id,
        user_id=actor_id,
        party=party.value,
    )
    session.add(acceptance)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "already_accepted", "You already accepted this version.") from exc

    await record_audit(
        session,
        event_type=AuditEventType.AGREEMENT_ACCEPTED,
        subject_type="agreement_version",
        subject_id=version.id,
        actor_user_id=actor_id,
        metadata={
            "agreement_id": str(agreement.id),
            "version": version.version_number,
            "party": party.value,
        },
    )

    acceptance_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AgreementAcceptance)
            .where(AgreementAcceptance.agreement_version_id == version.id)
        )
        or 0
    )
    if acceptance_count == 2 and agreement.status == AgreementStatus.PENDING_ACCEPTANCE.value:
        agreement.status = AgreementStatus.ACTIVE.value
        agreement.activated_at = datetime.now(UTC)
        if job.status == JobStatus.OPEN.value:
            job.status = JobStatus.IN_PROGRESS.value
        await record_audit(
            session,
            event_type=AuditEventType.AGREEMENT_ACTIVATED,
            subject_type="agreement",
            subject_id=agreement.id,
            actor_user_id=actor_id,
            metadata={"version": version.version_number},
        )

    await session.commit()
    return await _load_view(session, agreement)


async def list_agreements(
    session: AsyncSession,
    *,
    actor_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[AgreementView], int]:
    conditions = [
        or_(
            Agreement.owner_user_id == actor_id,
            Agreement.agent_owner_user_id == actor_id,
        )
    ]
    total = int(
        await session.scalar(select(func.count()).select_from(Agreement).where(*conditions)) or 0
    )
    agreements = list(
        await session.scalars(
            select(Agreement)
            .where(*conditions)
            .order_by(Agreement.updated_at.desc(), Agreement.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return [await _load_view(session, agreement) for agreement in agreements], total


async def get_agreement(
    session: AsyncSession,
    *,
    actor_id: UUID,
    agreement_id: UUID,
) -> AgreementView:
    agreement = await _get_participant_agreement(
        session,
        agreement_id=agreement_id,
        actor_id=actor_id,
    )
    return await _load_view(session, agreement)