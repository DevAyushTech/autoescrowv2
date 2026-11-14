from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubmissionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=5000)
    deliverables: list[dict[str, str]] = Field(min_length=1, max_length=30)


class SubmissionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(min_length=1, max_length=2000)


class DisputeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=10, max_length=2000)


class DisputeResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    resolution: str = Field(pattern="^(release|refund)$")
    note: str = Field(min_length=10, max_length=2000)


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    escrow_id: UUID
    job_id: UUID
    submitted_by_user_id: UUID
    attempt: int
    status: str
    summary: str
    deliverables: list[dict[str, str]]
    executor: str
    review_note: str | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


class DisputeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    submission_id: UUID
    escrow_id: UUID
    raised_by_user_id: UUID
    reason: str
    status: str
    resolution: str | None
    resolution_note: str | None
    resolved_by_user_id: UUID | None
    resolved_at: datetime | None
    created_at: datetime
