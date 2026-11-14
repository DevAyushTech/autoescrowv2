"""Deny Supabase Data API roles access to the public schema.

The application uses the FastAPI SIWE API and a direct PostgreSQL connection.
Public PostgREST access to the public schema is not part of the product's
authentication or data-access model.

Revision ID: 20261114_0003
Revises: 20261114_0002
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261114_0003"
down_revision: str | None = "20261114_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove inherited schema access so internal/platform default table grants
    # cannot accidentally expose future public-schema objects to Data API roles.
    # The application database role (postgres) and service_role have explicit
    # USAGE grants and keep them.
    op.execute("REVOKE USAGE ON SCHEMA public FROM PUBLIC")

    for role_name in ("anon", "authenticated"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I',
                        '{role_name}'
                    );
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    # Intentionally fail closed. Restoring public schema access is a separate
    # security decision and must never happen through an automatic rollback.
    raise RuntimeError(
        "This security migration is intentionally non-reversible. "
        "Do not restore public schema access to Supabase Data API roles."
    )
