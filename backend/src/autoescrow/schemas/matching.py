from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoescrow.models.enums import TaskCategory
from autoescrow.schemas.agents import AgentResponse
from autoescrow.schemas.common import normalize_currency, normalize_tags


class MatchingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: TaskCategory
    required_skills: list[str] = Field(min_length=1, max_length=20)
    required_capabilities: list[str] = Field(min_length=1, max_length=20)
    budget_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("required_skills", "required_capabilities")
    @classmethod
    def normalize_tags_field(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str) -> str:
        return normalize_currency(value)


class MatchReason(BaseModel):
    component: str
    score: float
    weight: float
    contribution: float
    explanation: str


class MatchResult(BaseModel):
    agent: AgentResponse
    score: float
    reasons: list[MatchReason]


class MatchingResponse(BaseModel):
    matches: list[MatchResult]
    weights: dict[str, float]
