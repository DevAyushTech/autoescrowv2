from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from autoescrow.api.dependencies import DbSession
from autoescrow.core.errors import ApiError
from autoescrow.models.enums import AgentAvailability, AgentStatus, PricingModel, TaskCategory
from autoescrow.schemas.agents import AgentListResponse, AgentResponse
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.services.agents import (
    AgentSort,
    SortOrder,
    get_marketplace_agent,
    list_marketplace_agents,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


@router.get(
    "/agents",
    response_model=AgentListResponse,
    summary="Discover active, publicly listed agents",
    responses=ERROR_RESPONSES,
)
async def discover_agents(
    session: DbSession,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category: TaskCategory | None = None,
    skill: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    capability: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    pricing_model: PricingModel | None = None,
    availability: AgentAvailability | None = None,
    status: AgentStatus | None = None,
    sort: AgentSort = "created_at",
    order: SortOrder = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentListResponse:
    agents, total = await list_marketplace_agents(
        session,
        search=search,
        category=category.value if category else None,
        skill=skill,
        capability=capability,
        pricing_model=pricing_model,
        availability=availability.value if availability else None,
        status=status,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return AgentListResponse(
        items=[
            AgentResponse.from_agent(agent, owner_display_name=owner_display_name)
            for agent, owner_display_name in agents
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=AgentResponse,
    summary="Read a public agent profile",
    responses=ERROR_RESPONSES,
)
async def get_public_agent(agent_id: UUID, session: DbSession) -> AgentResponse:
    result = await get_marketplace_agent(session, agent_id=agent_id)
    if result is None:
        raise ApiError(404, "agent_not_found", "Agent not found.")
    agent, owner_display_name = result
    return AgentResponse.from_agent(agent, owner_display_name=owner_display_name)
