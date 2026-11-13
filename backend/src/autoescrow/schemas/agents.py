from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoescrow.models.domain import Agent
from autoescrow.models.enums import AgentAvailability, AgentStatus, PricingModel, TaskCategory
from autoescrow.schemas.common import normalize_currency, normalize_tags


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    category: TaskCategory
    capabilities: list[str] = Field(min_length=1, max_length=20)
    skills: list[str] = Field(min_length=1, max_length=20)
    supported_task_types: list[str] = Field(min_length=1, max_length=20)
    pricing_model: PricingModel
    price_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    availability: AgentAvailability = AgentAvailability.AVAILABLE
    status: AgentStatus = AgentStatus.DRAFT
    is_public: bool = True

    @field_validator("capabilities", "skills", "supported_task_types")
    @classmethod
    def normalize_list_fields(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str) -> str:
        return normalize_currency(value)


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    category: TaskCategory | None = None
    capabilities: list[str] | None = Field(default=None, min_length=1, max_length=20)
    skills: list[str] | None = Field(default=None, min_length=1, max_length=20)
    supported_task_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    pricing_model: PricingModel | None = None
    price_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    availability: AgentAvailability | None = None
    status: AgentStatus | None = None
    is_public: bool | None = None

    @field_validator("capabilities", "skills", "supported_task_types")
    @classmethod
    def normalize_list_fields(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tags(value) if value is not None else None

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str | None) -> str | None:
        return normalize_currency(value) if value is not None else None

    @model_validator(mode="after")
    def require_a_field(self) -> "AgentUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one agent field must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AgentResponse(BaseModel):
    id: UUID
    name: str
    description: str
    category: str
    capabilities: list[str]
    skills: list[str]
    supported_task_types: list[str]
    pricing_model: str
    price_amount: Decimal
    currency: str
    availability: str
    status: str
    is_public: bool
    owner_display_name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_agent(cls, agent: Agent, *, owner_display_name: str) -> "AgentResponse":
        return cls(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            category=agent.category,
            capabilities=list(agent.capabilities),
            skills=list(agent.skills),
            supported_task_types=list(agent.supported_task_types),
            pricing_model=agent.pricing_model,
            price_amount=agent.price_amount,
            currency=agent.currency,
            availability=agent.availability,
            status=agent.status,
            is_public=agent.is_public,
            owner_display_name=owner_display_name,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


class AgentListResponse(BaseModel):
    items: list[AgentResponse]
    total: int
    limit: int
    offset: int

[executed on device: Astra (8d3c20ef-574d-4291-9eda-86715d4ce504)]