from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoescrow.models.enums import AuthNoncePurpose
from autoescrow.schemas.users import UserResponse


class NonceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(
        min_length=42,
        max_length=42,
        examples=["0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"],
    )
    chain_id: int = Field(gt=0, examples=[11155111])
    purpose: AuthNoncePurpose = AuthNoncePurpose.LOGIN

    @field_validator("address")
    @classmethod
    def reject_blank_address(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("address must not be blank")
        return value


class NonceResponse(BaseModel):
    address: str
    chain_id: int
    purpose: AuthNoncePurpose
    nonce: str
    message: str
    issued_at: datetime
    expires_at: datetime


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4096)
    signature: str = Field(min_length=2, max_length=256)

    @field_validator("message", "signature")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


class LoginResponse(TokenResponse):
    user: UserResponse


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=32, max_length=512)
