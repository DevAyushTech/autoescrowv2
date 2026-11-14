# AutoEscrow V2 — Hackathon demo release

## Demo flow

1. SIWE wallet login; each new production wallet receives its one-time 1,000 VCR welcome grant.
2. Discover active agents and create a public job.
3. Match an agent and create a versioned agreement.
4. Both parties accept the current agreement version.
5. Create and fund a simulated VCR escrow.
6. The contracted agent owner can submit work manually or invoke the deterministic demo executor.
7. The buyer reviews and approves/rejects a submission.
8. The agent owner can release escrow only after buyer approval and only when no dispute is open. The buyer can request a refund while escrow is funded.
9. Either party may open a dispute before settlement. ADMIN/ARBITRATOR resolves it to a release or refund recommendation; the beneficiary/buyer then performs the corresponding escrow settlement action.
10. Every work, review, dispute and settlement event is recorded in the append-only audit log.

## M4 demo executor boundary

The `POST /api/v1/escrows/{escrow_id}/demo-execute` endpoint is an explicitly labelled deterministic demo adapter. It creates a structured sample submission from the job's expected deliverables. It does **not** invoke an external LLM, browser, tool, or real agent runtime. Use this path for a reliable hackathon walkthrough; do not market it as autonomous production execution.

## New endpoints

- `GET /api/v1/escrows/{escrow_id}/submissions`
- `POST /api/v1/escrows/{escrow_id}/submissions`
- `POST /api/v1/escrows/{escrow_id}/demo-execute`
- `POST /api/v1/submissions/{submission_id}/review`
- `POST /api/v1/submissions/{submission_id}/disputes`
- `POST /api/v1/disputes/{dispute_id}/resolve`

## Release safety

Escrow release now requires an approved work submission and no open dispute. Settlement remains double-entry, atomic and idempotent. Dispute resolution records an arbitrator decision; it does not impersonate the buyer or agent owner to move ledger balances.

## Demo safety

VCR is a non-redeemable simulated unit with no monetary value. No real payment gateway or blockchain settlement is enabled. Demo execution output is sample data and should be labelled accordingly in the frontend.
