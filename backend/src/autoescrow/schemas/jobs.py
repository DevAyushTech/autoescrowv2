from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from autoescrow.models.domain import Job
from autoescrow.models.enums import JobStatus, JobVisibility, TaskCategory
from autoescrow.schemas.common import normalize_currency, normalize_items, normalize_tags


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    category: TaskCategory
    required_skills: list[str] = Field(min_length=1, max_length=20)
    required_capabilities: list[str] = Field(min_length=1, max_length=20)
    budget_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    deadline: AwareDatetime
    expected_deliverables: list[str] = Field(min_length=1, max_length=30)
    status: JobStatus = JobStatus.DRAFT
    visibility: JobVisibility = JobVisibility.PRIVATE

    @field_validator("required_skills", "required_capabilities")
    @classmethod
    def normalize_tags_field(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @field_validator("expected_deliverables")
    @classmethod
    def normalize_deliverables(cls, value: list[str]) -> list[str]:
        return normalize_items(value)

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str) -> str:
        return normalize_currency(value)

    @field_validator("status")
    @classmethod
    def validate_initial_status(cls, value: JobStatus) -> JobStatus:
        if value not in {JobStatus.DRAFT, JobStatus.OPEN}:
            raise ValueError("a new job must be draft or open")
        return value


class JobUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    category: TaskCategory | None = None
    required_skills: list[str] | None = Field(default=None, min_length=1, max_length=20)
    required_capabilities: list[str] | None = Field(default=None, min_length=1, max_length=20)
    budget_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    deadline: AwareDatetime | None = None
    expected_deliverables: list[str] | None = Field(default=None, min_length=1, max_length=30)
    status: JobStatus | None = None
    visibility: JobVisibility | None = None

    @field_validator("required_skills", "required_capabilities")
    @classmethod
    def normalize_tags_field(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tags(value) if value is not None else None

    @field_validator("expected_deliverables")
    @classmethod
    def normalize_deliverables(cls, value: list[str] | None) -> list[str] | None:
        return normalize_items(value) if value is not None else None

    @field_validator("currency")
    @classmethod
    def normalize_currency_field(cls, value: str | None) -> str | None:
        return normalize_currency(value) if value is not None else None

    @model_validator(mode="after")
    def require_a_field(self) -> "JobUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one job field must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class JobResponse(BaseModel):
    id: UUID
    title: str
    description: str
    category: str
    required_skills: list[str]
    required_capabilities: list[str]
    budget_amount: Decimal
    currency: str
    deadline: datetime
    expected_deliverables: list[str]
    status: str
    visibility: str
    owner_display_name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Job, *, owner_display_name: str) -> "JobResponse":
        return cls(
            id=job.id,
            title=job.title,
            description=job.description,
            category=job.category,
            required_skills=list(job.required_skills),
            required_capabilities=list(job.required_capabilities),
            budget_amount=job.budget_amount,
            currency=job.currency,
            deadline=job.deadline,
            expected_deliverables=list(job.expected_deliverables),
            status=job.status,
            visibility=job.visibility,
            owner_display_name=owner_display_name,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int
