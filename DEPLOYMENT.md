# AutoEscrow V2 — zero-cost demo deployment

This deployment is for a hackathon demo only, not production financial infrastructure.
All escrow activity remains simulated VCR virtual credits with no monetary value.

## Free-service layout

- **API:** Render Free Web Service, Docker runtime, Singapore region.
- **PostgreSQL:** Supabase Free project.
- **Redis:** Render Key Value Free instance, created by the Blueprint and connected over Render's internal network.
- **Frontend:** existing Vercel deployment at https://autoescrowfrontend.vercel.app/.

Current free-tier constraints matter:
- Render Free web services spin down after 15 minutes without inbound traffic and can take about a minute to wake up. Free Render Postgres expires after 30 days, so this setup intentionally uses an external database.
- Supabase Free includes a 500 MB database and may pause projects after one week of inactivity.
- Render Key Value Free has no persistence; it is used only for short-lived cache/rate-limit/nonce data. A restart can invalidate active login challenges.
- This is suitable for a demo with synthetic data, not an SLA-backed production service. Confirm provider limits before relying on them.

Provider documentation:
- Render free limits: https://render.com/docs/free
- Render Blueprint configuration: https://render.com/docs/blueprint-spec
- Supabase pricing/limits: https://supabase.com/pricing

## 1. Connect the GitHub repository to Render

Repository: https://github.com/DevAyushTech/autoescrowv2
Branch: `main`

In Render, connect the GitHub repository and create a new Blueprint using the root `render.yaml`.
Choose the free plan. The Blueprint sets the Docker build context to `backend/`.
The Docker image runs `alembic upgrade head` before starting Uvicorn and listens on
Render's `PORT` environment variable.

## 2. Create a Supabase database

1. Create a Free Supabase project and save the database password securely.
2. In Project Settings → Database, copy the connection string (use the Session Pooler
   connection if your host/network requires IPv4).
3. Convert the scheme to `postgresql+asyncpg://` and preserve the rest of the host,
   port, database name, username and URL-encoded password.
4. Set the complete value as Render's `AUTOESCROW_DATABASE_URL` secret.

Do not commit this URL or put it in frontend variables.

## 3. Redis is provisioned by the Render Blueprint

The Blueprint creates a free Render Key Value instance in Singapore and wires its
internal connection string into `AUTOESCROW_REDIS_URL`. No separate Redis account or
secret is required. Its free plan is non-persistent, which is acceptable for cache,
rate-limit counters and short-lived SIWE nonces, but active challenges can be lost
when the service restarts.

## 4. Set Render environment variables

The Blueprint generates `AUTOESCROW_JWT_SECRET`. Set these values in the Render
service environment after the service has a public hostname:

| Variable | Value |
| --- | --- |
| `AUTOESCROW_ENVIRONMENT` | `production` |
| `AUTOESCROW_DEBUG` | `false` |
| `AUTOESCROW_DEMO_CREDIT_ENABLED` | `false` |
| `AUTOESCROW_DATABASE_URL` | Supabase `postgresql+asyncpg://...` connection string |
| `AUTOESCROW_REDIS_URL` | Automatically wired from the Render Key Value service |
| `AUTOESCROW_JWT_SECRET` | Generate a random secret of at least 32 bytes; Blueprint generates it |
| `AUTOESCROW_SIWE_DOMAIN` | Bare hostname only, e.g. `autoescrow-api.onrender.com` |
| `AUTOESCROW_SIWE_URI` | Exact HTTPS origin, e.g. `https://autoescrow-api.onrender.com` |
| `AUTOESCROW_CORS_ALLOWED_ORIGINS` | JSON array containing the Vercel frontend origin |

Keep demo-credit issuance disabled in production. Never add a real wallet private key,
payment credential, or blockchain RPC key for this simulated M3 deployment.

## 5. Point the frontend to the API

Set the frontend's API base URL to the Render service origin plus `/api/v1`
using the frontend's existing environment variable. The frontend source is not part
of this backend repository, so its exact variable name must be confirmed in that app.
After changing it, redeploy the Vercel frontend and verify SIWE domain/URI and CORS
origins match the deployed domains exactly.

## 6. Smoke tests

After Render reports a successful deploy, verify:

- `https://YOUR-API.onrender.com/health`
- `https://YOUR-API.onrender.com/ready`
- `https://YOUR-API.onrender.com/docs` is disabled in production by design.
- `https://YOUR-API.onrender.com/openapi.json` is disabled in production by design.

Use the frontend or a local API client to test authenticated endpoints. The API must
never report an on-chain transaction or real-money settlement.

## Deployment limitations / pending account actions

This repository contains the Render Blueprint, but cloud resources cannot be created
until the owner connects the GitHub repository to Render and supplies the Supabase
database connection string in Render's secret settings. Render Key Value is created
automatically by the Blueprint. Do not paste secrets in chat. Once the public Render
hostname exists, set the matching SIWE domain/URI and frontend API base URL.
