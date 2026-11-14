from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autoescrow.infrastructure.database import Base
from autoescrow.models.identity import utc_now


class WorkSubmission(Base):
    __tablename__ = "work_submissions"
    __table_args__ = (
        UniqueConstraint("escrow_id", "attempt", name="uq_work_submission_escrow_attempt"),
        CheckConstraint("attempt > 0", name="attempt_positive"),
        CheckConstraint(
            "status IN ('submitted', 'approved', 'rejected', 'disputed')", name="status_valid"
        ),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name="summary_length"),
        CheckConstraint("jsonb_typeof(deliverables) = 'array'", name="deliverables_array"),
        Index("ix_work_submissions_escrow_created", "escrow_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    escrow_id: Mapped[UUID] = mapped_column(ForeignKey("escrows.id", ondelete="RESTRICT"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    submitted_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    attempt: Mapped[int] = mapped_column(default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), default="submitted", server_default="submitted")
    summary: Mapped[str] = mapped_column(String(5000))
    deliverables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    executor: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")
    review_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class WorkDispute(Base):
    __tablename__ = "work_disputes"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_work_disputes_submission"),
        CheckConstraint("status IN ('open', 'resolved')", name="status_valid"),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('release', 'refund')", name="resolution_valid"
        ),
        Index("ix_work_disputes_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("work_submissions.id", ondelete="RESTRICT")
    )
    escrow_id: Mapped[UUID] = mapped_column(ForeignKey("escrows.id", ondelete="RESTRICT"))
    raised_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
