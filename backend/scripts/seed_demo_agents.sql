-- AutoEscrow V2 demo marketplace seed.
-- Safe to re-run: uses deterministic IDs and only updates these seeded records.
-- Creates a non-login demo owner (no email, wallet, session, or credentials).
-- Profiles are illustrative marketplace data, not connected execution runtimes.

DO $seed$
DECLARE
    demo_owner_id CONSTANT uuid := 'd0000000-0000-4000-8000-000000000001';
BEGIN
    INSERT INTO public.users (id, display_name, email, status)
    VALUES (demo_owner_id, 'AutoEscrow Demo Studio', NULL, 'active')
    ON CONFLICT (id) DO NOTHING;

    IF NOT EXISTS (
        SELECT 1
        FROM public.users
        WHERE id = demo_owner_id
          AND display_name = 'AutoEscrow Demo Studio'
          AND email IS NULL
    ) THEN
        RAISE EXCEPTION 'Reserved AutoEscrow demo owner ID is already used by another account';
    END IF;

    INSERT INTO public.user_roles (user_id, role_id, assigned_by_user_id)
    SELECT demo_owner_id, roles.id, NULL
    FROM public.roles
    WHERE roles.code = 'AGENT_OWNER'
    ON CONFLICT (user_id, role_id) DO NOTHING;

    INSERT INTO public.agents (
        id, owner_user_id, name, description, category,
        capabilities, skills, supported_task_types,
        pricing_model, price_amount, currency,
        availability, status, is_public
    )
    VALUES
    (
        'd0000000-0000-4000-8000-000000000101',
        demo_owner_id,
        'Demo - Code Review Agent',
        'Demo marketplace profile for a code-review service. Illustrates static analysis, maintainability checks, bug triage, security review, and test-coverage feedback. This is sample registry data and is not connected to an executing agent runtime.',
        'development',
        '["static_analysis", "logic_review", "security_review", "test_coverage_analysis"]'::jsonb,
        '["python", "typescript", "react", "fastapi", "code_review", "testing"]'::jsonb,
        '["code_review", "bug_triage", "quality_audit"]'::jsonb,
        'fixed', 25.00, 'VCR', 'available', 'active', TRUE
    ),
    (
        'd0000000-0000-4000-8000-000000000102',
        demo_owner_id,
        'Demo - Research and Analysis Agent',
        'Demo marketplace profile for structured research, source synthesis, data interpretation, and evidence-led reporting. This is sample registry data and is not connected to an executing agent runtime.',
        'research',
        '["web_research", "source_synthesis", "data_interpretation", "report_generation"]'::jsonb,
        '["research", "data_analysis", "technical_writing", "market_research", "reporting"]'::jsonb,
        '["technical_research", "market_research", "comparative_analysis"]'::jsonb,
        'fixed', 18.00, 'VCR', 'available', 'active', TRUE
    ),
    (
        'd0000000-0000-4000-8000-000000000103',
        demo_owner_id,
        'Demo - Smart Contract Audit Agent',
        'Demo marketplace profile for Solidity source review, access-control analysis, reentrancy checks, and common smart-contract risk identification. This is sample registry data and is not connected to an executing agent runtime or a certified security audit.',
        'development',
        '["solidity_review", "access_control_analysis", "reentrancy_review", "risk_assessment"]'::jsonb,
        '["solidity", "smart_contracts", "ethereum", "security_review", "reentrancy"]'::jsonb,
        '["smart_contract_review", "vulnerability_analysis", "solidity_audit"]'::jsonb,
        'fixed', 40.00, 'VCR', 'available', 'active', TRUE
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        category = EXCLUDED.category,
        capabilities = EXCLUDED.capabilities,
        skills = EXCLUDED.skills,
        supported_task_types = EXCLUDED.supported_task_types,
        pricing_model = EXCLUDED.pricing_model,
        price_amount = EXCLUDED.price_amount,
        currency = EXCLUDED.currency,
        availability = EXCLUDED.availability,
        status = EXCLUDED.status,
        is_public = EXCLUDED.is_public,
        updated_at = now()
    WHERE public.agents.owner_user_id = EXCLUDED.owner_user_id;

    IF (
        SELECT count(*)
        FROM public.agents
        WHERE owner_user_id = demo_owner_id
          AND id IN (
              'd0000000-0000-4000-8000-000000000101',
              'd0000000-0000-4000-8000-000000000102',
              'd0000000-0000-4000-8000-000000000103'
          )
          AND status = 'active'
          AND is_public IS TRUE
    ) <> 3 THEN
        RAISE EXCEPTION 'Demo agent seed did not create exactly three active public agents';
    END IF;
END;
$seed$;
