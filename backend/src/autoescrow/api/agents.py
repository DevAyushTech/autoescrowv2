from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from autoescrow.api.dependencies import CurrentUser, DbSession, require_permission
from autoescrow.core.permissions import Permission
from autoescrow.models.domain import Agent
from autoescrow.schemas.agents import (
    AgentCreateRequest,
    AgentListResponse,
    AgentResponse,
    AgentUpdateRequest,
)
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.services.agents import (
    create_agent,
    deactivate_agent,
    get_owned_agent,
    list_owned_agents,
    update_agent,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _response(agent: Agent, owner_display_name: str) -> AgentResponse:
    return AgentResponse.from_agent(agent, owner_display_name=owner_display_name)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentResponse,
    summary="Register an agent",
    responses=ERROR_RESPONSES,
)
async def register_agent(
    payload: AgentCreateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGENT_WRITE)),
    ],
) -> AgentResponse:
    agent = await create_agent(session, owner=principal.user, payload=payload)
    response.headers["Cache-Control"] = "no-store"
    return _response(agent, principal.user.display_name)


@router.get(
    "",
    response_model=AgentListResponse,
    summary="List the authenticated user's agents",
    responses=ERROR_RESPONSES,
)
async def list_my_agents(
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGENT_READ)),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentListResponse:
    agents, total = await list_owned_agents(
        session,
        owner_id=principal.user_id,
        limit=limit,
        offset=offset,
    )
    response.headers["Cache-Control"] = "no-store"
    return AgentListResponse(
        items=[
            AgentResponse.from_agent(agent, owner_display_name=principal.user.display_name)
            for agent in agents
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Read one of the authenticated user's agents",
    responses=ERROR_RESPONSES,
)
async def get_my_agent(
    agent_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGENT_READ)),
    ],
) -> AgentResponse:
    agent = await get_owned_agent(
        session,
        owner_id=principal.user_id,
        agent_id=agent_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(agent, principal.user.display_name)


@router.patch(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Update one of the authenticated user's agents",
    responses=ERROR_RESPONSES,
)
async def update_my_agent(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGENT_WRITE)),
    ],
) -> AgentResponse:
    agent = await update_agent(
        session,
        owner_id=principal.user_id,
        agent_id=agent_id,
        payload=payload,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(agent, principal.user.display_name)


@router.post(
    "/{agent_id}/deactivate",
    response_model=AgentResponse,
    summary="Deactivate one of the authenticated user's agents",
    responses=ERROR_RESPONSES,
)
async def deactivate_my_agent(
    agent_id: UUID,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.AGENT_WRITE)),
    ],
) -> AgentResponse:
    agent = await deactivate_agent(
        session,
        owner_id=principal.user_id,
        agent_id=agent_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return _response(agent, principal.user.display_name)

[executed on device: Astra (8d3c20ef-574d-4291-9eda-86715d4ce504)]