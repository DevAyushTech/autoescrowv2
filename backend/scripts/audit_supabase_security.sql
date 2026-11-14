-- Read-only Supabase security posture audit for AutoEscrow.
-- Expected: RLS on application tables, no anon/authenticated table rights,
-- no inherited RPC EXECUTE rights, and pinned trigger-function search paths.
-- This script intentionally does not read application user data.

SELECT
    c.relname AS table_name,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls,
    CASE WHEN to_regrole('anon') IS NULL THEN NULL
         ELSE has_table_privilege('anon', c.oid, 'SELECT') END AS anon_select,
    CASE WHEN to_regrole('anon') IS NULL THEN NULL
         ELSE has_table_privilege('anon', c.oid, 'INSERT') END AS anon_insert,
    CASE WHEN to_regrole('authenticated') IS NULL THEN NULL
         ELSE has_table_privilege('authenticated', c.oid, 'SELECT') END AS authenticated_select,
    CASE WHEN to_regrole('authenticated') IS NULL THEN NULL
         ELSE has_table_privilege('authenticated', c.oid, 'INSERT') END AS authenticated_insert
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND c.relname IN (
      'alembic_version', 'roles', 'users', 'auth_nonces', 'refresh_sessions',
      'user_roles', 'wallets', 'audit_events', 'agents', 'jobs', 'agreements',
      'agreement_versions', 'agreement_acceptances', 'ledger_accounts', 'escrows',
      'ledger_transactions', 'ledger_entries'
  )
ORDER BY c.relname;

SELECT
    p.proname AS function_name,
    p.proconfig AS function_settings,
    CASE WHEN to_regrole('anon') IS NULL THEN NULL
         ELSE has_function_privilege('anon', p.oid, 'EXECUTE') END AS anon_execute,
    CASE WHEN to_regrole('authenticated') IS NULL THEN NULL
         ELSE has_function_privilege('authenticated', p.oid, 'EXECUTE') END
         AS authenticated_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
      'prevent_audit_event_mutation',
      'prevent_agreement_version_mutation',
      'autoescrow_reject_ledger_mutation',
      'autoescrow_check_ledger_balance'
  )
ORDER BY p.proname;

-- Review default grants for newly created objects owned by the role used by
-- Alembic migrations. anon/authenticated/PUBLIC should not receive access.
SELECT
    defaclrole::regrole AS owner_role,
    defaclnamespace::regnamespace AS schema_name,
    defaclobjtype AS object_type,
    defaclacl AS default_acl
FROM pg_default_acl
WHERE defaclnamespace = 'public'::regnamespace
ORDER BY owner_role, object_type;
