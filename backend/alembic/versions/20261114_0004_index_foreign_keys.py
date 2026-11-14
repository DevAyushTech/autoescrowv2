"""Add indexes for uncovered application foreign keys.

Revision ID: 20261114_0004
Revises: 20261114_0003
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261114_0004"
down_revision: str | None = "20261114_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These indexes cover FK columns used in creator/account relationships.
    # IF NOT EXISTS keeps the migration safe after the matching live indexes
    # were applied directly as an additive, low-risk optimization.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agreement_versions_created_by_user_id "
        "ON public.agreement_versions (created_by_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agreements_created_by_user_id "
        "ON public.agreements (created_by_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_escrows_escrow_account_id "
        "ON public.escrows (escrow_account_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_escrows_escrow_account_id")
    op.execute("DROP INDEX IF EXISTS public.ix_agreements_created_by_user_id")
    op.execute("DROP INDEX IF EXISTS public.ix_agreement_versions_created_by_user_id")
