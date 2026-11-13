from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from autoescrow.models.domain import AgreementAcceptance, AgreementVersion
from autoescrow.schemas.common import normalize_currency, normalize_items


class AgreementTerms(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope: str = Field(min_length=1, max_length=5000)
    deliverables: list[str] = Field(min_length=1, max_length=20)
    budget_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    deadline: AwareDatetime
    responsibilities: list[str] = Field(min_length=1, max_length=20)
    acceptance_conditions: list[str] = Field(min_length=1, max_length=20)

    @field_validator("deliverables", "responsibilities", "acceptance_conditions")
    @classmethod
    def normalize_list_fields(cls, value: list[str]) -> list[str]:
        return normalize_items(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str) -> str:
        return normalize_currency(value)


class AgreementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    agent_id: UUID
    terms: AgreementTerms


class AgreementVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version_number: int = Field(ge=1)
    terms: AgreementTerms


class AgreementAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_number: int = Field(ge=1)


class AgreementVersionResponse(BaseModel):
    id: UUID
    version_number: int
    terms: AgreementTerms
    terms_hash: str
    created_by_user_id: UUID
    created_at: datetime

    @classmethod
    def from_version(cls, version: AgreementVersion) -> "AgreementVersionResponse":
        return cls(
            id=version.id,
            version_number=version.version_number,
            terms=AgreementTerms.model_validate(version.terms),
            terms_hash=version.terms_hash,
            created_by_user_id=version.created_by_user_id,
            created_at=version.created_at,
        )


class AgreementAcceptanceResponse(BaseModel):
    id: UUID
    version_number: int
    user_id: UUID
    party: str
    accepted_at: datetime

    @classmethod
    def from_acceptance(
        cls,
        acceptance: AgreementAcceptance,
        *,
        version_number: int,
    ) -> "AgreementAcceptanceResponse":
        return cls(
            id=acceptance.id,
            version_number=version_number,
            user_id=acceptance.user_id,
            party=acceptance.party,
            accepted_at=acceptance.accepted_at,
        )


class AgreementResponse(BaseModel):
    id: UUID
    job_id: UUID
    agent_id: UUID
    owner_user_id: UUID
    agent_owner_user_id: UUID
    status: str
    current_version_number: int
    current_version: AgreementVersionResponse
    versions: list[AgreementVersionResponse]
    acceptances: list[AgreementAcceptanceResponse]
    created_at: datetime
    updated_at: datetime


class AgreementListResponse(BaseModel):
    items: list[AgreementResponse]
    total: int
    limit: int
    offset: int


__all__ = [
    "AgreementAcceptRequest",
    "AgreementAcceptanceResponse",
    "AgreementCreateRequest",
    "AgreementListResponse",
    "AgreementResponse",
    "AgreementTerms",
    "AgreementVersionCreateRequest",
    "AgreementVersionResponse",
]
