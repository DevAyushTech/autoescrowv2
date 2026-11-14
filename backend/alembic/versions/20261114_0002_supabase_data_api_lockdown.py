"""Lock down the Supabase Data API surface for application tables.

The API uses custom SIWE authentication and a direct SQLAlchemy PostgreSQL
connection. It does not use Supabase Auth JWTs or supabase-js. Therefore the
public Data API roles must not access private application tables directly.

Revision ID: 20261114_0002
Revises: aeac5730b0a6
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261114_0002"
down_revision: str | None = "aeac5730b0a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPLICATION_TABLES = (
    "alembic_version",
    "roles",
    "users",
    "auth_nonces",
    "refresh_sessions",
    "user_roles",
    "wallets",
    "audit_events",
    "agents",
    "jobs",
    "agreements",
    "agreement_versions",
    "agreement_acceptances",
    "ledger_accounts",
    "escrows",
    "ledger_transactions",
    "ledger_entries",
)

_TRIGGER_FUNCTIONS = (
    "prevent_audit_event_mutation",
    "prevent_agreement_version_mutation",
    "autoescrow_reject_ledger_mutation",
    "autoescrow_check_ledger_balance",
)


def upgrade() -> None:
    # Enable RLS for all application tables. No Supabase Auth policies are
    # created because app users authenticate with SIWE handled by FastAPI.
    # The FastAPI/Alembic database role owns these tables and remains able to
    # use them; table owners bypass RLS unless FORCE ROW LEVEL SECURITY is set.
    for table_name in _APPLICATION_TABLES:
        op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')

    # Keep Supabase's public API roles away from the application schema.
    # Role checks allow the same migration to run on plain local PostgreSQL,
    # where Supabase's anon/authenticated roles may not exist.
    for role_name in ("anon", "authenticated"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role_name}';
                    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {role_name}';
                    EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {role_name}';
                    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM {role_name}';
                    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM {role_name}';
                    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {role_name}';
                END IF;
            END
            $$;
            """
        )

    # Pin search_path for trigger functions so object resolution cannot be
    # changed by a caller-controlled search_path or temporary object shadowing.
    for function_name in _TRIGGER_FUNCTIONS:
        op.execute(
            f"ALTER FUNCTION public.{function_name}() "
            "SET search_path = pg_catalog, public, pg_temp"
        )


def downgrade() -> None:
    # Intentionally fail closed. Restoring anon/authenticated grants or
    # disabling RLS would reintroduce the audited exposure. A rollback must
    # be a separately reviewed security change, not an automatic downgrade.
    raise RuntimeError(
        "This security migration is intentionally non-reversible. "
        "Do not restore public Data API access to application tables."
    )
