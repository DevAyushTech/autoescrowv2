from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from autoescrow.models.identity import User


class UserResponse(BaseModel):
    id: UUID
    display_name: str
    email: str | None
    status: str
    roles: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_user(cls, user: User, roles: Iterable[str]) -> "UserResponse":
        return cls(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            roles=sorted(roles),
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None

    @model_validator(mode="after")
    def require_a_field(self) -> "ProfileUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one profile field must be provided")
        return self
