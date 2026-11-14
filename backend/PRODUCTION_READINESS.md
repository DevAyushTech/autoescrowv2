# AutoEscrow V2 — Backend production-readiness boundary

## Supported production scope

The current backend is a production-deployed **simulated escrow application**. Its supported scope is wallet authentication, user and wallet management, agent registry/marketplace, jobs, deterministic matching, versioned agreements, and a double-entry VCR ledger with simulated escrow funding/release/refund.

VCR is a non-redeemable demo unit with no monetary value. This service is not financial custody, a payment processor, or a real-money escrow provider.

## Verified controls

- SIWE login challenges are one-time, expiring, exact-message-bound and concurrency protected.
- Access tokens are short-lived; refresh tokens rotate and are stored hashed; session revocation is checked on authenticated requests.
- RBAC is loaded from PostgreSQL for each authenticated request.
- Sensitive authentication endpoints use Redis-backed rate limits and fail closed if Redis is unavailable.
- PostgreSQL RLS is enabled on all 17 public tables. The `anon` and `authenticated` Supabase roles have no direct SELECT/INSERT table privileges; the API connects through its server-side database connection.
- Ledger postings are double-entry, atomic, append-only, idempotent and protected against negative available balances and duplicate settlement.
- Production docs/OpenAPI are disabled; CORS is explicit; unexpected errors use a stable generic response.
- Production configuration now rejects debug mode, SQL echo, DEBUG logging, non-HTTPS SIWE origins, local SIWE hostnames and non-HTTPS CORS origins.
- Response headers include no-sniff, frame denial, referrer and permissions policies; production responses additionally receive HSTS and a restrictive API Content-Security-Policy.

## Current verification

Run from `backend/`:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest -q
alembic check
docker build -t autoescrow-backend:local .
```

A passing local test suite does not replace production backup/restore drills, provider availability, or external security review.

## Deployment gates not solvable by application code alone

The currently configured Render web service is on the Free plan and the existing deployment documentation describes a zero-cost demo stack. Free hosting does not provide the availability guarantees needed for a business-critical service. Supabase/Redis plan capabilities, backup retention, point-in-time recovery, restore procedures and resource limits must be confirmed in the provider dashboards.

Before treating the service as a business-critical production system:

1. Move the API to an appropriate paid Render plan with an availability target and adequate resources.
2. Confirm the Supabase database plan, automated backup retention and point-in-time recovery; perform and document a restore drill.
3. Use a Redis plan with suitable availability and eviction/persistence settings for rate-limit and short-lived authentication state.
4. Configure uptime/error monitoring and alert delivery to an owner-controlled destination.
5. Establish secret rotation, incident response, access review and deployment rollback procedures.
6. Obtain an independent security review before handling real customer assets or financial obligations.

These are external operational/billing decisions. This code change does not upgrade provider plans, alter billing, or enable real-money processing.

## Hackathon demo scope now implemented

- Work submissions are stored as workflow records with attempt numbers and deliverable metadata.
- Buyer approval/rejection is explicit and audit logged.
- Escrow release requires a buyer-approved submission and no open dispute.
- Either escrow participant can raise a dispute before settlement; ADMIN/ARBITRATOR can record a release/refund resolution.
- The deterministic demo executor creates a structured sample submission from job deliverables. It does not call an external LLM, browser, or autonomous agent runtime; the UI must label it as a demo execution.
- New work tables have RLS enabled and no direct anon/authenticated table privileges.

The hackathon demo flow is documented in backend/HACKATHON_DEMO.md. This is a complete simulated workflow demonstration, not a claim of autonomous real-world task execution, independent verification, or real-money custody.

## Still outside the demo release

A production autonomous agent runtime, external tool/browser execution, cryptographic proof of work, automated independent verification, reputation scoring, real dispute arbitration operations, real-money payments, and financial custody are not implemented. Do not represent these as available.
