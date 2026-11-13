from typing import Any, Literal
from uuid import UUID

from sqlalchemy import asc, desc, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.models.domain import Agent
from autoescrow.models.enums import AgentStatus, PricingModel
from autoescrow.models.identity import User
from autoescrow.schemas.agents import AgentCreateRequest, AgentUpdateRequest
from autoescrow.services.audit import AuditEventType, record_audit

AgentSort = Literal["created_at", "name", "price_amount", "updated_at"]
SortOrder = Literal["asc", "desc"]
MarketplaceAgent = tuple[Agent, str]

_ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    AgentStatus.DRAFT.value: frozenset({AgentStatus.DRAFT.value, AgentStatus.ACTIVE.value}),
    AgentStatus.ACTIVE.value: frozenset({AgentStatus.ACTIVE.value, AgentStatus.INACTIVE.value}),
    AgentStatus.INACTIVE.value: frozenset({AgentStatus.INACTIVE.value, AgentStatus.ACTIVE.value}),
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _owned_agent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID,
) -> Agent:
    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.owner_user_id == owner_id)
    )
    if agent is None:
        raise ApiError(404, "agent_not_found", "Agent not found.")
    return agent


async def create_agent(
    session: AsyncSession,
    *,
    owner: User,
    payload: AgentCreateRequest,
) -> Agent:
    if payload.status == AgentStatus.INACTIVE:
        raise ApiError(422, "invalid_agent_status", "A new agent cannot be inactive.")

    agent = Agent(
        owner_user_id=owner.id,
        name=payload.name,
        description=payload.description,
        category=payload.category.value,
        capabilities=payload.capabilities,
        skills=payload.skills,
        supported_task_types=payload.supported_task_types,
        pricing_model=payload.pricing_model.value,
        price_amount=payload.price_amount,
        currency=payload.currency,
        availability=payload.availability.value,
        status=payload.status.value,
        is_public=payload.is_public,
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "agent_name_unavailable",
            "You already have an agent with that name.",
        ) from exc

    await record_audit(
        session,
        event_type=AuditEventType.AGENT_CREATED,
        subject_type="agent",
        subject_id=agent.id,
        actor_user_id=owner.id,
        metadata={
            "status": agent.status,
            "category": agent.category,
            "pricing_model": agent.pricing_model,
        },
    )
    await session.commit()
    return agent


async def list_owned_agents(
    session: AsyncSession,
    *,
    owner_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[Agent], int]:
    conditions = [Agent.owner_user_id == owner_id]
    total = int(
        await session.scalar(select(func.count()).select_from(Agent).where(*conditions)) or 0
    )
    agents = list(
        await session.scalars(
            select(Agent)
            .where(*conditions)
            .order_by(Agent.created_at.desc(), Agent.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return agents, total


async def get_owned_agent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID,
) -> Agent:
    return await _owned_agent(session, owner_id=owner_id, agent_id=agent_id)


async def update_agent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID,
    payload: AgentUpdateRequest,
) -> Agent:
    agent = await _owned_agent(session, owner_id=owner_id, agent_id=agent_id)
    updates = payload.model_dump(exclude_unset=True)
    requested_status = updates.pop("status", None)

    if requested_status is not None and requested_status != agent.status:
        normalized_status = AgentStatus(requested_status).value
        if normalized_status not in _ALLOWED_STATUS_TRANSITIONS[agent.status]:
            raise ApiError(
                409,
                "invalid_agent_transition",
                f"Agent cannot transition from {agent.status} to {normalized_status}.",
            )
        agent.status = normalized_status
        if normalized_status == AgentStatus.INACTIVE.value and "is_public" not in updates:
            agent.is_public = False

    for field, value in updates.items():
        if field in {"category", "pricing_model", "availability"} and value is not None:
            value = str(value)
        setattr(agent, field, value)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "agent_name_unavailable",
            "You already have an agent with that name.",
        ) from exc

    await record_audit(
        session,
        event_type=AuditEventType.AGENT_UPDATED,
        subject_type="agent",
        subject_id=agent.id,
        actor_user_id=owner_id,
        metadata={"status": agent.status, "fields": sorted(updates)},
    )
    await session.commit()
    return agent


async def deactivate_agent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID,
) -> Agent:
    agent = await _owned_agent(session, owner_id=owner_id, agent_id=agent_id)
    if agent.status != AgentStatus.INACTIVE.value:
        agent.status = AgentStatus.INACTIVE.value
        agent.is_public = False
        await session.flush()
        await record_audit(
            session,
            event_type=AuditEventType.AGENT_DEACTIVATED,
            subject_type="agent",
            subject_id=agent.id,
            actor_user_id=owner_id,
            metadata={"status": agent.status},
        )
        await session.commit()
    return agent


def _marketplace_conditions(
    *,
    category: str | None,
    skill: str | None,
    capability: str | None,
    pricing_model: PricingModel | None,
    availability: str | None,
    status: AgentStatus | None,
) -> list[Any]:
    conditions: list[Any] = [
        Agent.status == AgentStatus.ACTIVE.value,
        Agent.is_public.is_(True),
    ]
    if status is not None and status != AgentStatus.ACTIVE:
        conditions.append(false())
    if category is not None:
        conditions.append(Agent.category == category)
    if pricing_model is not None:
        conditions.append(Agent.pricing_model == pricing_model.value)
    if availability is not None:
        conditions.append(Agent.availability == availability)
    if skill:
        conditions.append(Agent.skills.contains([skill.strip().lower()]))
    if capability:
        conditions.append(Agent.capabilities.contains([capability.strip().lower()]))
    return conditions


async def list_marketplace_agents(
    session: AsyncSession,
    *,
    search: str | None,
    category: str | None,
    skill: str | None,
    capability: str | None,
    pricing_model: PricingModel | None,
    availability: str | None,
    status: AgentStatus | None,
    sort: AgentSort,
    order: SortOrder,
    limit: int,
    offset: int,
) -> tuple[list[MarketplaceAgent], int]:
    conditions = _marketplace_conditions(
        category=category,
        skill=skill,
        capability=capability,
        pricing_model=pricing_model,
        availability=availability,
        status=status,
    )
    if search:
        pattern = f"%{_escape_like(search.strip())}%"
        conditions.append(
            or_(
                Agent.name.ilike(pattern, escape="\\"),
                Agent.description.ilike(pattern, escape="\\"),
            )
        )

    total = int(
        await session.scalar(select(func.count()).select_from(Agent).where(*conditions)) or 0
    )

    sort_columns = {
        "created_at": Agent.created_at,
        "name": Agent.name,
        "price_amount": Agent.price_amount,
        "updated_at": Agent.updated_at,
    }
    ordering = asc if order == "asc" else desc
    statement = (
        select(Agent, User.display_name)
        .join(User, User.id == Agent.owner_user_id)
        .where(*conditions)
        .order_by(ordering(sort_columns[sort]), Agent.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = await session.execute(statement)
    return [(row[0], row[1]) for row in rows.all()], total


async def get_marketplace_agent(
    session: AsyncSession,
    *,
    agent_id: UUID,
) -> MarketplaceAgent | None:
    row = (
        await session.execute(
            select(Agent, User.display_name)
            .join(User, User.id == Agent.owner_user_id)
            .where(
                Agent.id == agent_id,
                Agent.status == AgentStatus.ACTIVE.value,
                Agent.is_public.is_(True),
            )
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]
