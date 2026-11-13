# AutoEscrow V2 Backend

Trust, verification and secure settlement infrastructure for autonomous AI agents.

This repository currently contains **Milestone M1: Identity & Access** on top of the
completed M0 foundations. Authentication uses Sign-In with Ethereum (EIP-4361) on
Ethereum Sepolia (`11155111`), short-lived JWT access tokens, rotating opaque refresh
tokens, wallet management and role-based access control.

Marketplace, agents, jobs, agreements, escrow, contracts, AI verification, disputes
and reputation are intentionally **not implemented** in M1. Do not build M2 against
this milestone without starting the next milestone explicitly.

## Requirements

- Python 3.12
- Docker with Compose (for PostgreSQL and Redis)
- A SEPOLIA wallet for manual login testing; tests use deterministic throwaway keys
- No production secrets or real private keys are stored in this repository

On Debian/Ubuntu, install the `python3.12-venv` and `python3-pip` packages if the
interpreter does not include `pip` or virtual-environment support.

## Quick start with Docker Compose

```bash
make env                     # creates .env and generates AUTOESCROW_JWT_SECRET
docker compose up --build -d
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/ready
```

API documentation is available at `http://localhost:8000/docs` outside production.
Production mode disables `/docs`, `/redoc` and `/openapi.json`.

The Compose stack contains PostgreSQL, Redis, a one-shot Alembic migration service
and the API. PostgreSQL development credentials are intentionally local-only. The
database initialization creates an isolated `autoescrow_test` database for tests.

## Local development

```bash
make env
make install
docker compose up -d db redis
make migrate
make run
```

Dependencies are constrained by `backend/requirements.lock`. Regenerate it after an
intentional dependency upgrade with `make lock`.

Run quality checks:

```bash
make lint
make typecheck
make test
make check       # lint + typecheck + tests
```

`make test` applies pending Alembic migrations to the dedicated test database before
running the suite. The default test URLs are:

```text
postgresql+asyncpg://autoescrow:autoescrow@127.0.0.1:5432/autoescrow_test
redis://127.0.0.1:6379/15
```

If you have an existing PostgreSQL volume created before this setup, create the test
database once:

```bash
docker compose exec db createdb -U autoescrow autoescrow_test
```

## Configuration

Configuration is loaded from environment variables with the `AUTOESCROW_` prefix.
A root `.env` file is supported for local development and must never be committed.

| Variable | Required | Purpose |
| --- | --- | --- |
| `AUTOESCROW_DATABASE_URL` | yes | PostgreSQL async SQLAlchemy URL (`postgresql+asyncpg://...`) |
| `AUTOESCROW_REDIS_URL` | yes | Redis URL (`redis://...` or `rediss://...`) |
| `AUTOESCROW_JWT_SECRET` | yes | JWT signing secret; at least 32 bytes. Generate with `make env` |
| `AUTOESCROW_ENVIRONMENT` | no | `local`, `test`, `staging` or `production` (default `local`) |
| `AUTOESCROW_LOG_LEVEL` | no | Log level (default `INFO`) |
| `AUTOESCROW_LOG_JSON` | no | JSON logs (default `true`) |
| `AUTOESCROW_DEBUG` | no | FastAPI debug mode (default `false`) |
| `AUTOESCROW_API_V1_PREFIX` | no | API prefix (default `/api/v1`) |
| `AUTOESCROW_ETHEREUM_CHAIN_ID` | no | Supported chain; M1 accepts only `11155111` |
| `AUTOESCROW_SIWE_DOMAIN` | no | SIWE domain, e.g. `localhost:8000` |
| `AUTOESCROW_SIWE_URI` | no | SIWE URI; its authority must match `SIWE_DOMAIN` |
| `AUTOESCROW_SIWE_STATEMENT` | no | Human-readable SIWE statement |
| `AUTOESCROW_ACCESS_TOKEN_TTL_SECONDS` | no | Access-token lifetime (300–3600, default `900`) |
| `AUTOESCROW_REFRESH_TOKEN_TTL_SECONDS` | no | Refresh-token lifetime (default 30 days) |
| `AUTOESCROW_AUTH_NONCE_TTL_SECONDS` | no | SIWE challenge lifetime (default `300`) |
| `AUTOESCROW_CORS_ALLOWED_ORIGINS` | no | JSON array of browser origins |
| `AUTOESCROW_RATE_LIMIT_NONCE_REQUESTS` | no | Nonce requests per IP per window (default `10`) |
| `AUTOESCROW_RATE_LIMIT_VERIFY_REQUESTS` | no | Verify requests per IP per window (default `20`) |
| `AUTOESCROW_RATE_LIMIT_REFRESH_REQUESTS` | no | Refresh requests per IP per window (default `30`) |

Additional pool, timeout and per-scope rate-limit settings are defined in
`backend/src/autoescrow/core/config.py`. The default CORS origins include local
development origins and `https://autoescrowfrontend.vercel.app`; credentials are
disabled because tokens are transported explicitly, not via cookies.

## Authentication flow

1. `POST /api/v1/auth/wallet/nonce` with a wallet address and chain ID. The API
   creates a one-time EIP-4361 message and stores its nonce and exact message.
2. The wallet signs the returned message exactly as provided using EIP-191.
3. `POST /api/v1/auth/wallet/verify` with that message and signature. The API checks
   the domain, URI, chain, address, statement, timestamps and signature, consumes the
   nonce once, then finds or creates the user and primary wallet.
4. The response contains a short-lived JWT access token and an opaque refresh token.
   Send the access token as `Authorization: Bearer <token>`. Send the refresh token
   only in the JSON body of `POST /api/v1/auth/token/refresh`.
5. Every refresh rotates the token. Reusing a rotated or revoked refresh token
   revokes the entire session family, including its replacement.
6. Logout revokes the current session family. Revoked sessions are rejected
   immediately by authenticated endpoints, even if the access JWT has not expired.

Refresh tokens are stored only as SHA-256 hashes. Signatures, raw refresh tokens,
private keys and SIWE message contents are never written to audit metadata.

Wallet linking uses a user-bound `purpose=wallet_link` nonce and requires a Bearer
access token. A login challenge cannot be reused for wallet linking.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/wallet/nonce` | Create a SIWE challenge (`login` or authenticated `wallet_link`) |
| `POST` | `/api/v1/auth/wallet/verify` | Verify a login signature and issue tokens |
| `POST` | `/api/v1/auth/token/refresh` | Rotate a refresh token |
| `POST` | `/api/v1/auth/logout` | Revoke the current session family |
| `GET` | `/api/v1/auth/sessions` | List active sessions; marks the current one |
| `POST` | `/api/v1/auth/sessions/{session_id}/revoke` | Revoke one of the caller's session families |
| `GET` | `/api/v1/users/me` | Read the authenticated profile and roles |
| `PATCH` | `/api/v1/users/me` | Update display name and email |
| `GET` | `/api/v1/users/me/wallets` | List the caller's verified wallets |
| `POST` | `/api/v1/users/me/wallets` | Link a wallet with a signed challenge |
| `DELETE` | `/api/v1/users/me/wallets/{wallet_id}` | Unlink a wallet (the last wallet is protected) |
| `GET` | `/health`, `/api/v1/health` | Liveness probe |
| `GET` | `/ready`, `/api/v1/ready` | Readiness probe (PostgreSQL + Redis) |

All errors use the stable envelope:

```json
{
  "error": {
    "code": "invalid_access_token",
    "message": "Access token is invalid."
  }
}
```

Validation errors additionally include a `details` array. Unexpected exceptions
return a generic `500` response and are logged with a traceback.

## Roles and permissions

Four roles are seeded by the M1 migration:

- `USER` — standard authenticated user
- `AGENT_OWNER` — future agent ownership
- `ARBITRATOR` — includes audit read permission
- `ADMIN` — all permissions, including role management foundations

Role permissions are reloaded from PostgreSQL on every authenticated request. Roles
embedded in an access token are informational only and are never trusted for
authorization. There is intentionally **no public role-management endpoint** in M1.
Privileged role assignment is exposed through the controlled
`autoescrow.services.roles` service; bootstrap the first administrator through an
operational SQL/service task, not through a client API.

## Security notes

- SIWE messages are single-use and bound to the exact stored message and nonce.
- Concurrent nonce use and refresh rotation are serialized with database row locks;
  tests verify that only one request wins.
- Access JWTs enforce algorithm, issuer, audience, expiry, required claims, subject,
  session ID and token type. HS256 is the only accepted algorithm.
- Session revocation is checked on every authenticated request.
- Redis rate limiting is fail-closed: if Redis is unavailable, security-sensitive
  endpoints return `503 rate_limit_unavailable` with `Retry-After`.
- Audit events are append-only; a PostgreSQL trigger rejects `UPDATE` and `DELETE`.
- CORS uses an explicit allowlist, authorization headers, and no credentials.
- Security headers include `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a strict request ID.
- Authentication is Sepolia-only in M1. Ethereum mainnet support is intentionally
  rejected by configuration validation.

## Repository layout

```text
backend/
  alembic/                 Alembic environment and migrations
    versions/              M0 baseline and M1 identity/access migration
  src/autoescrow/
    api/                   Versioned HTTP routes and dependencies
    core/                  Config, logging, middleware, errors, RBAC mappings
    infrastructure/        Database, Redis, SIWE import, rate limiting
    models/                SQLAlchemy identity, RBAC and audit models
    schemas/               Pydantic request/response contracts
    services/              Authentication, SIWE, sessions, users, roles, audit
    main.py                Application factory and lifespan
  scripts/                 Developer environment helper scripts
  tests/                   Unit, security and integration tests
  requirements.lock        Pinned dependency constraints generated by `make lock`
compose.yml                Local PostgreSQL, Redis and API stack
infra/postgres/init.sql    Creates the isolated test database
```

## M1 acceptance status

- [x] SIWE/EIP-4361 login on Sepolia with strict message binding
- [x] One-time nonces with expiry, replay protection and concurrency locking
- [x] HS256 JWT access tokens with strict claim validation
- [x] Opaque rotating refresh tokens stored only as hashes
- [x] Refresh reuse detection and whole-family revocation
- [x] Session listing, revocation and immediate access-token invalidation
- [x] Wallet create/list/remove with user-bound signed challenges
- [x] RBAC with roles reloaded from PostgreSQL per request
- [x] Redis rate limiting, fail-closed, with stable error codes
- [x] Append-only audit events protected by a database trigger
- [x] Alembic M1 migration, clean upgrade/downgrade cycle and no schema drift
- [x] Automated M1 security and integration tests
- [x] Docker Compose stack builds and runs the full M1 flow
- [x] CI configured for lint, formatting, typing, migrations, tests and Docker build
