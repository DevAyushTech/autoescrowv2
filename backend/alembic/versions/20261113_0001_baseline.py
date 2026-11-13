"""Establish the baseline schema revision.

Business tables are introduced by the domain milestones. This baseline exists so
that every environment has an explicit, reproducible Alembic starting point.

Revision ID: 20261113_0001
Revises:
Create Date: 2026-11-13

"""

from collections.abc import Sequence

revision: str = "20261113_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
