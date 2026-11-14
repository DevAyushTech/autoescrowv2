# Database security hardening

## What changed

The application uses FastAPI + SQLAlchemy over a direct PostgreSQL connection.
Wallet authentication is implemented by the API with SIWE. The frontend does
not use Supabase Auth JWTs or Supabase `supabase-js` to access application
tables.

The migration `20261114_0002` therefore:

- Enables row-level security on the 17 application tables in `public`.
- Revokes `USAGE` on the `public` schema from `PUBLIC`, `anon` and
  `authenticated`, preventing access to future objects even if an internal
  platform-owner default ACL is broader than the application-owner ACL.
- Revokes table, sequence, and function privileges from the Supabase
  `anon` and `authenticated` roles, when those roles exist.
- Removes those roles from default privileges for objects created by the
  migration owner in `public`.
- Revokes inherited `PUBLIC` function execution, including for future functions
  created by the current migration owner.
- Pins the search path for the four immutable/audit/ledger trigger functions.
- Leaves `service_role` privileges unchanged. Never expose its secret key to
  browser code or commit it to source control.

No RLS policies are added. A Supabase Auth policy based on `auth.uid()` would
not represent this application's SIWE users. The API remains responsible for
authentication and ownership checks. RLS is enabled as defense in depth, and
the API's direct database role must remain the table owner or have an
equivalent explicitly reviewed database privilege. Do not connect the browser
directly to these tables.

The current Supabase audit also found a separate platform-managed default
privilege entry owned by the internal `supabase_admin` role. The connected
`postgres` migration role is neither a member of nor permitted to assume that
role, so this migration cannot change that internal role's defaults. The
schema-usage lockdown added in `20261114_0003` provides a second barrier:
`anon` and `authenticated` cannot reach objects in `public` even if future
internal defaults grant table privileges. A privileged platform operator
could still explicitly restore schema access; any such change must be reviewed.
The current application tables and migration-owner defaults are hardened.

The migration deliberately does not use `FORCE ROW LEVEL SECURITY`: the
application's SQLAlchemy role owns the tables and must continue to execute
queries after the RLS boundary is enabled. Do not weaken the role or move the
API to an ordinary non-owner role without first designing and testing a
separate database authorization model.

## Deployment

The backend Docker image runs `alembic upgrade head` before starting Uvicorn.
A normal successful Render deployment applies the migration. The migration is
idempotent for RLS enablement, grants, default privileges, and function
settings. It skips Supabase roles on plain local PostgreSQL where they do not
exist.

## Verification

Run the read-only SQL in `backend/scripts/audit_supabase_security.sql`
against the Supabase project after deployment.

Expected result:

- All 17 application tables report `relrowsecurity = true`.
- `anon` and `authenticated` have no `USAGE` on the `public` schema and no
  table privileges on those tables.
- The backend database role and `service_role` retain explicit schema `USAGE`.
- The four listed trigger functions report
  `search_path=pg_catalog, public, pg_temp`.
- `anon` and `authenticated` do not inherit `EXECUTE` on those functions.
- The public marketplace and health endpoints still work because FastAPI
  accesses PostgreSQL through its direct database connection.

Also verify:

1. `GET /health` returns 200.
2. `GET /api/v1/marketplace/agents` returns the expected list response.
3. Unauthenticated protected API routes still return 401.
4. A browser using only the Supabase publishable/anon key cannot read or
   mutate private application tables.

Do not run write probes against production tables. Use a disposable local
Supabase project for insert/update/delete denial tests.

## Operational limitations

- Render Free web services and free Key Value are demo-grade: cold starts and
  non-persistent Redis can interrupt availability and invalidate rate-limit
  counters or short-lived login challenges.
- The current escrow ledger is simulated VCR, not real-money custody or a
  deployed smart contract.
- Production financial custody, arbitration, or proof-of-work verification
  requires separate audited systems and a formal security review.


## Foreign-key index coverage

Revision `20261114_0004` adds B-tree indexes for three previously uncovered
foreign-key columns:

- `agreement_versions.created_by_user_id`
- `agreements.created_by_user_id`
- `escrows.escrow_account_id`

These are additive indexes only. The migration does not remove any of the
33 indexes previously reported as unused; unused-index advice must be evaluated
against representative production workload before considering removal.
