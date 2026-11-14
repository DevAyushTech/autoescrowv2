"""work submission and dispute workflow

Revision ID: 6fae04b9b266
Revises: eb7109768762
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6fae04b9b266"
down_revision: str | None = "eb7109768762"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "escrow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.escrows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(20), server_default="submitted", nullable=False),
        sa.Column("summary", sa.String(5000), nullable=False),
        sa.Column("deliverables", postgresql.JSONB(), nullable=False),
        sa.Column("executor", sa.String(30), server_default="manual", nullable=False),
        sa.Column("review_note", sa.String(2000), nullable=True),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("escrow_id", "attempt", name="uq_work_submission_escrow_attempt"),
        sa.CheckConstraint("attempt > 0", name="attempt_positive"),
        sa.CheckConstraint(
            "status IN ('submitted', 'approved', 'rejected', 'disputed')", name="status_valid"
        ),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name="summary_length"),
        sa.CheckConstraint("jsonb_typeof(deliverables) = 'array'", name="deliverables_array"),
        schema="public",
    )
    op.create_index(
        "ix_work_submissions_escrow_created",
        "work_submissions",
        ["escrow_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "work_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.work_submissions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "escrow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.escrows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "raised_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("resolution", sa.String(20), nullable=True),
        sa.Column("resolution_note", sa.String(2000), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("submission_id", name="uq_work_disputes_submission"),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="status_valid"),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('release', 'refund')", name="resolution_valid"
        ),
        schema="public",
    )
    op.create_index(
        "ix_work_disputes_status_created",
        "work_disputes",
        ["status", "created_at"],
        schema="public",
    )
    op.execute("ALTER TABLE public.work_submissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.work_disputes ENABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.work_submissions, public.work_disputes FROM PUBLIC"
    )
    for role in ("anon", "authenticated"):
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.work_submissions, "
            f"public.work_disputes FROM %I', '{role}'); END IF; END $$;"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Work history is append-only; do not automatically remove production work/dispute records."
    )
