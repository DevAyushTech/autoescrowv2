# AutoEscrow V2 — M3: Simulated Escrow & Ledger

You are working in the existing repository at /mnt/d/DevProjects/AutoEscrow V1. Implement M3 directly. This is a zero-budget hackathon prototype: **virtual credits only**. No real funds, ETH, tokens, blockchain, RPC, wallet signing, paid services, or cash-out.

## First: inspect and protect existing work
1. Read README, Makefile, compose.yml, backend requirements/lock, all current models, enums, schemas, services, API routers, migrations, audit implementation, permissions/dependencies, and tests.
2. M0, M1, and M2 are already implemented but M2 changes are currently uncommitted. Preserve all existing user changes. Do not reset, checkout, clean, stash, or overwrite unrelated files. Do not modify .continue/ unless strictly required (it is untracked and may contain user configuration).
3. Confirm current Alembic head and current tests before changes. Report baseline.
4. Follow existing architecture, naming, async SQLAlchemy conventions, Pydantic version, permissions, error handling, audit, and test fixtures. Do not add dependencies unless genuinely necessary.

## M3 scope
Build a production-minded, deterministic **simulated escrow and double-entry virtual-credit ledger**.

### Accounting invariants
- Currency/unit must be unmistakably virtual, e.g. VCR / virtual_credits. API and docs must explicitly mark values as simulated and not redeemable.
- Implement balanced journal entries: every posted transaction has debit total == credit total, in the same virtual unit. Prefer integer minor units (e.g. whole credits or fixed smallest unit) rather than floating point.
- Model ledger accounts and immutable journal entries/lines. Use append-only database enforcement/triggers where consistent with existing audit protections. Never edit/delete posted entries; corrections use compensating transactions.
- Maintain available/reserved/escrow balances through ledger-derived or safely maintained account balances. Never allow negative spendable balance.
- Use database transactions and row-level locking/atomic conditional updates to prevent concurrent double-spend and duplicate release/refund. Ensure every multi-line journal posting is all-or-nothing.
- Add idempotency keys for all money-moving commands. Same key + same request returns the original result; same key + different payload returns a conflict. Enforce uniqueness in DB.
- Add deterministic transaction references and append-only audit events for all lifecycle/accounting actions.
- Never expose a public arbitrary mint/credit endpoint. Seed/demo-credit issuance must be restricted to local/test/demo mode and an explicit controlled operation/fixture; disabled in production. Add a config flag with safe default false outside local/test. Document exact safeguards.
- Do not trust client-supplied owner, amount, currency, or state. Derive actor from authenticated principal and validate agreement ownership/acceptance and exact amount/currency against persisted agreement.

### Escrow lifecycle
- Create escrow only from an existing agreement accepted by both parties and associated job/agent. Enforce one escrow per agreement unless existing product design explicitly requires otherwise.
- Verify buyer/client identity and agent owner/beneficiary identity using M2 ownership relationships.
- Escrow amount and virtual unit must match accepted agreement exactly; no client-side override.
- Explicit state machine, e.g. CREATED -> FUNDED -> RELEASED or REFUNDED; add CANCELLED only when valid before funding. Do not permit arbitrary status patches or terminal-state transitions.
- Funding moves buyer available virtual credits into escrow holding atomically.
- Release moves escrow holding to the intended agent-owner available balance exactly once.
- Refund moves escrow holding back to the original buyer exactly once.
- Keep release/refund authority conservative and explicit. If M3 does not yet have verification/dispute modules, allow only a clearly documented authorized demo/admin policy path or narrowly scoped buyer/agent mutual authorization; do not pretend work verification exists. Do not implement M4 execution, verification, disputes, or reputation in this milestone.
- Enforce access control on all reads and actions; prevent IDOR across users.
- Return clear, stable API errors for insufficient funds, invalid state, unauthorized actor, duplicate idempotency conflict, and agreement mismatch.

### API
Design routes consistent with existing /api/v1 patterns. At minimum support:
- Create escrow from accepted agreement.
- List current user's escrows with pagination/filters if consistent with existing API.
- Get escrow detail only for an authorized party/admin.
- Fund escrow using virtual credits.
- Release and refund using explicit authorization policy.
- Get current user's virtual-credit balances and ledger transaction history.
- Any demo-credit bootstrap route must be disabled by default in production and strongly restricted; prefer local seed/test fixtures over a public endpoint.

Use Pydantic request/response schemas and OpenAPI descriptions that clearly say SIMULATED / VIRTUAL CREDITS / NO REAL VALUE. Never label it ETH, USD, fiat, cryptocurrency, or real on-chain settlement.

### Configuration and docs
- Add a safe configuration flag for demo-credit provisioning. Validate that it cannot be enabled when environment=production.
- Update .env.example, README and relevant API docs with a clear disclosure: all M3 balances and settlements are simulated virtual credits with no monetary value; no blockchain transaction occurs.
- Keep local Docker Compose setup; do not require external services, paid RPC, cloud accounts, API keys, or payment credentials.
- No real secrets or private keys in code or docs.

## Tests — required
Add meaningful unit/integration tests covering:
- Balanced journal posting and rejection of unbalanced journal.
- Insufficient funds; no negative balances.
- Funding success and exact balance movements.
- Release and refund success with exact balance movements.
- Double release, double refund, invalid transitions, funding after terminal state.
- Idempotent retry and same key/different payload conflict.
- Concurrent funding/release/refund attempts cannot double-spend or pay twice.
- Atomic rollback on partial failure.
- Agreement must be accepted by both parties; amount/unit cannot be altered.
- Unauthorized access and cross-user IDOR prevention.
- Posted journal immutability and audit trail.
- Demo credit provisioning disabled in production and inaccessible to ordinary users.
- Existing M0/M1/M2 tests continue to pass.

## Quality gates
- Run the complete test suite, Ruff lint/format checks, strict mypy, Alembic upgrade/check, Docker build and Compose health/readiness checks using existing project commands.
- Test migration upgrade from current M2 head; do not rewrite old migrations. Check fresh database migration too if feasible.
- Verify OpenAPI route/schema registration.
- Do not claim a check passed unless you ran it and captured its output. If an environment limitation blocks a check, report it precisely.
- Do not commit, push, deploy, reset, or alter Git remotes.
- Do not begin M4.
- At the end provide: files changed, migration ID/parent, endpoints, accounting invariants, tests and exact results, Docker/health status, limitations, and explicit M3 acceptance checklist. Leave M3 unaccepted pending user review.

## Execution approach
Implement in small coherent steps. Inspect existing code before choosing exact model/table names. Reuse current audit, auth, DB, permissions, and error abstractions. Avoid broad refactors. Preserve compatibility with existing M2 API contracts. If a design ambiguity risks weakening accounting/security, choose the safer conservative behavior and document it rather than inventing a permissive shortcut.
