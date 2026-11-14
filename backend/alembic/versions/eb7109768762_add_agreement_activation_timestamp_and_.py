"""add agreement activation timestamp and unique active-job index

Revision ID: eb7109768762
Revises: 20261114_0004
Create Date: 2026-11-14 07:44:00.211977

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "eb7109768762"
down_revision: str | None = "20261114_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Activation is recorded when the current agreement version is accepted by both
    # parties. Existing active agreements predate this timestamp, so backfill them
    # from updated_at as a conservative historical marker.
    op.add_column(
        "agreements",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE public.agreements "
        "SET activated_at = updated_at "
        "WHERE status = 'active' AND activated_at IS NULL"
    )
    op.create_index(
        "uq_agreements_job_activated",
        "agreements",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("activated_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agreements_job_activated", table_name="agreements")
    op.drop_column("agreements", "activated_at")
