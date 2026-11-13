from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from autoescrow.api.dependencies import CurrentUser, DbSession, SettingsDep, require_permission
from autoescrow.core.permissions import Permission
from autoescrow.schemas.agents import AgentResponse
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.matching import (
    MatchingRequest,
    MatchingResponse,
    MatchReason,
    MatchResult,
)
from autoescrow.services.jobs import get_owned_job
from autoescrow.services.matching import (
    MatchingWeights,
    MatchRequirements,
    MatchScore,
    match_agents,
    requirements_from_job,
)

router = APIRouter(prefix="/matching", tags=["matching"])


def _response(
    matches: list[MatchScore],
    weights: dict[str, float],
) -> MatchingResponse:
    return MatchingResponse(
        matches=[
            MatchResult(
                agent=AgentResponse.from_agent(
                    item.agent,
                    owner_display_name=item.owner_display_name,
                ),
                score=item.score,
                reasons=[
                    MatchReason(
                        component=reason.component,
                        score=reason.score,
                        weight=reason.weight,
                        contribution=reason.contribution,
                        explanation=reason.explanation,
                    )
                    for reason in item.reasons
                ],
            )
            for item in matches
        ],
        weights={name: round(value, 6) for name, value in weights.items()},
    )


@router.post(
    "/agents",
    response_model=MatchingResponse,
    summary="Match agents against explicit job requirements",
    responses=ERROR_RESPONSES,
)
async def match_agents_to_requirements(
    payload: MatchingRequest,
    session: DbSession,
    settings: SettingsDep,
    _principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.MATCHING_READ)),
    ],
) -> MatchingResponse:
    requirements = MatchRequirements(
        category=payload.category.value,
        required_skills=tuple(payload.required_skills),
        required_capabilities=tuple(payload.required_capabilities),
        budget_amount=payload.budget_amount,
        currency=payload.currency,
        limit=payload.limit,
    )
    matches, weights = await match_agents(
        session,
        requirements=requirements,
        weights=MatchingWeights.from_settings(settings),
    )
    return _response(matches, weights)


@router.post(
    "/jobs/{job_id}",
    response_model=MatchingResponse,
    summary="Match agents against one of the authenticated user's jobs",
    responses=ERROR_RESPONSES,
)
async def match_agents_to_job(
    job_id: UUID,
    session: DbSession,
    settings: SettingsDep,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.MATCHING_READ)),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MatchingResponse:
    job = await get_owned_job(session, owner_id=principal.user_id, job_id=job_id)
    requirements = replace(requirements_from_job(job), limit=limit)
    matches, weights = await match_agents(
        session,
        requirements=requirements,
        weights=MatchingWeights.from_settings(settings),
    )
    return _response(matches, weights)
