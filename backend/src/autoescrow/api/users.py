from typing import Annotated

from fastapi import APIRouter, Depends, Response

from autoescrow.api.dependencies import CurrentUser, DbSession, require_permission
from autoescrow.core.permissions import Permission
from autoescrow.schemas.common import ERROR_RESPONSES
from autoescrow.schemas.users import ProfileUpdateRequest, UserResponse
from autoescrow.services.users import update_profile

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Read the authenticated profile",
    responses=ERROR_RESPONSES,
)
async def get_me(
    response: Response,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.PROFILE_READ)),
    ],
) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.from_user(principal.user, principal.roles)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update the authenticated profile",
    responses=ERROR_RESPONSES,
)
async def update_me(
    payload: ProfileUpdateRequest,
    response: Response,
    session: DbSession,
    principal: Annotated[
        CurrentUser,
        Depends(require_permission(Permission.PROFILE_UPDATE)),
    ],
) -> UserResponse:
    user = await update_profile(
        session,
        user=principal.user,
        fields_set=payload.model_fields_set,
        display_name=payload.display_name,
        email=str(payload.email) if payload.email else None,
    )
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.from_user(user, principal.roles)
