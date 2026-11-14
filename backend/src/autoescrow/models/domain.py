from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autoescrow.infrastructure.database import Base
from autoescrow.models.enums import (
    AgentStatus,
    JobStatus,
    JobVisibility,
)
from autoescrow.models.identity import utc_now

_AGENT_STATUS_VALUES = "'draft', 'active', 'inactive'"
_AGENT_AVAILABILITY_VALUES = "'available', 'limited', 'unavailable'"
_PRICING_MODEL_VALUES = "'fixed', 'hourly', 'negotiable'"
_TASK_CATEGORY_VALUES = (
    "'development', 'design', 'writing', 'data', 'marketing', 'research', 'operations', 'other'"
)
_JOB_STATUS_VALUES = "'draft', 'open', 'in_progress', 'cancelled', 'completed'"
_JOB_VISIBILITY_VALUES = "'private', 'public'"
_AGREEMENT_STATUS_VALUES = "'pending_acceptance', 'active', 'cancelled'"
_AGREEMENT_PARTY_VALUES = "'owner', 'agent_owner'"


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "uq_agents_owner_name_lower",
            "owner_user_id",
            text("lower(name)"),
            unique=True,
        ),
        Index("ix_agents_owner_user_id", "owner_user_id"),
        Index("ix_agents_public_discovery", "status", "is_public", "category", "availability"),
        Index("ix_agents_category", "category"),
        Index("ix_agents_pricing_model", "pricing_model"),
        Index("ix_agents_skills_gin", "skills", postgresql_using="gin"),
        Index("ix_agents_capabilities_gin", "capabilities", postgresql_using="gin"),
        Index(
            "ix_agents_supported_task_types_gin",
            "supported_task_types",
            postgresql_using="gin",
        ),
        CheckConstraint(f"status IN ({_AGENT_STATUS_VALUES})", name="status_valid"),
        CheckConstraint(
            f"availability IN ({_AGENT_AVAILABILITY_VALUES})",
            name="availability_valid",
        ),
        CheckConstraint(
            f"pricing_model IN ({_PRICING_MODEL_VALUES})",
            name="pricing_model_valid",
        ),
        CheckConstraint(f"category IN ({_TASK_CATEGORY_VALUES})", name="category_valid"),
        CheckConstraint("price_amount > 0", name="price_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("char_length(name) BETWEEN 2 AND 120", name="name_length"),
        CheckConstraint(
            "char_length(description) BETWEEN 1 AND 5000",
            name="description_length",
        ),
        CheckConstraint("jsonb_typeof(skills) = 'array'", name="skills_array"),
        CheckConstraint(
            "jsonb_array_length(skills) BETWEEN 1 AND 20",
            name="skills_length",
        ),
        CheckConstraint(
            "jsonb_typeof(capabilities) = 'array'",
            name="capabilities_array",
        ),
        CheckConstraint(
            "jsonb_array_length(capabilities) BETWEEN 1 AND 20",
            name="capabilities_length",
        ),
        CheckConstraint(
            "jsonb_typeof(supported_task_types) = 'array'",
            name="task_types_array",
        ),
        CheckConstraint(
            "jsonb_array_length(supported_task_types) BETWEEN 1 AND 20",
            name="task_types_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[list[str]] = mapped_column(JSONB)
    skills: Mapped[list[str]] = mapped_column(JSONB)
    supported_task_types: Mapped[list[str]] = mapped_column(JSONB)
    pricing_model: Mapped[str] = mapped_column(String(20))
    price_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(
        String(20),
        default="available",
        server_default="available",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=AgentStatus.DRAFT.value,
        server_default=AgentStatus.DRAFT.value,
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_owner_user_id", "owner_user_id"),
        Index("ix_jobs_public_discovery", "status", "visibility", "category"),
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_required_skills_gin", "required_skills", postgresql_using="gin"),
        Index(
            "ix_jobs_required_capabilities_gin",
            "required_capabilities",
            postgresql_using="gin",
        ),
        CheckConstraint(f"status IN ({_JOB_STATUS_VALUES})", name="status_valid"),
        CheckConstraint(f"visibility IN ({_JOB_VISIBILITY_VALUES})", name="visibility_valid"),
        CheckConstraint(f"category IN ({_TASK_CATEGORY_VALUES})", name="category_valid"),
        CheckConstraint("budget_amount > 0", name="budget_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("char_length(title) BETWEEN 3 AND 200", name="title_length"),
        CheckConstraint(
            "char_length(description) BETWEEN 1 AND 10000",
            name="description_length",
        ),
        CheckConstraint(
            "jsonb_typeof(required_skills) = 'array'",
            name="required_skills_array",
        ),
        CheckConstraint(
            "jsonb_array_length(required_skills) BETWEEN 1 AND 20",
            name="required_skills_length",
        ),
        CheckConstraint(
            "jsonb_typeof(required_capabilities) = 'array'",
            name="required_capabilities_array",
        ),
        CheckConstraint(
            "jsonb_array_length(required_capabilities) BETWEEN 1 AND 20",
            name="required_capabilities_length",
        ),
        CheckConstraint(
            "jsonb_typeof(expected_deliverables) = 'array'",
            name="deliverables_array",
        ),
        CheckConstraint(
            "jsonb_array_length(expected_deliverables) BETWEEN 1 AND 30",
            name="deliverables_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    required_skills: Mapped[list[str]] = mapped_column(JSONB)
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB)
    budget_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_deliverables: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(30),
        default=JobStatus.DRAFT.value,
        server_default=JobStatus.DRAFT.value,
    )
    visibility: Mapped[str] = mapped_column(
        String(20),
        default=JobVisibility.PRIVATE.value,
        server_default=JobVisibility.PRIVATE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )


class Agreement(Base):
    __tablename__ = "agreements"
    __table_args__ = (
        UniqueConstraint("job_id", "agent_id", name="uq_agreements_job_id_agent_id"),
        Index("ix_agreements_job_id", "job_id"),
        Index("ix_agreements_agent_id", "agent_id"),
        Index("ix_agreements_owner_user_id", "owner_user_id"),
        Index("ix_agreements_agent_owner_user_id", "agent_owner_user_id"),
        Index("ix_agreements_created_by_user_id", "created_by_user_id"),
        Index("ix_agreements_status_updated_at", "status", "updated_at"),
        CheckConstraint(f"status IN ({_AGREEMENT_STATUS_VALUES})", name="status_valid"),
        CheckConstraint("current_version_number > 0", name="version_positive"),
        CheckConstraint("owner_user_id <> agent_owner_user_id", name="distinct_parties"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    agent_owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending_acceptance",
        server_default="pending_acceptance",
    )
    current_version_number: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )


class AgreementVersion(Base):
    __tablename__ = "agreement_versions"
    __table_args__ = (
        UniqueConstraint(
            "agreement_id",
            "version_number",
            name="uq_agreement_versions_agreement_id_version_number",
        ),
        Index("ix_agreement_versions_agreement_id", "agreement_id"),
        Index("ix_agreement_versions_created_by_user_id", "created_by_user_id"),
        CheckConstraint("version_number > 0", name="version_positive"),
        CheckConstraint("terms_hash ~ '^[0-9a-f]{64}$'", name="terms_hash_format"),
        CheckConstraint("jsonb_typeof(terms) = 'object'", name="terms_object"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agreement_id: Mapped[UUID] = mapped_column(ForeignKey("agreements.id", ondelete="RESTRICT"))
    version_number: Mapped[int]
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_hash: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class AgreementAcceptance(Base):
    __tablename__ = "agreement_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "agreement_version_id",
            "user_id",
            name="uq_agreement_acceptances_agreement_version_id_user_id",
        ),
        UniqueConstraint(
            "agreement_version_id",
            "party",
            name="uq_agreement_acceptances_agreement_version_id_party",
        ),
        Index("ix_agreement_acceptances_agreement_version_id", "agreement_version_id"),
        Index("ix_agreement_acceptances_user_id", "user_id"),
        CheckConstraint(
            f"party IN ({_AGREEMENT_PARTY_VALUES})",
            name="party_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agreement_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    party: Mapped[str] = mapped_column(String(20))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


__all__ = [
    "Agent",
    "Agreement",
    "AgreementAcceptance",
    "AgreementVersion",
    "Job",
]
