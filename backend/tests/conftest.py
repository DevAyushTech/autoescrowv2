import os
from collections.abc import AsyncIterator, Generator
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]

TEST_JWT_SECRET = "test-only-jwt-secret-with-more-than-32-bytes"
TEST_DATABASE_URL = "postgresql+asyncpg://autoescrow:autoescrow@127.0.0.1:5432/autoescrow_test"
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"

os.environ.setdefault("AUTOESCROW_ENVIRONMENT", "test")
os.environ.setdefault("AUTOESCROW_DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("AUTOESCROW_REDIS_URL", TEST_REDIS_URL)
os.environ.setdefault("AUTOESCROW_JWT_SECRET", TEST_JWT_SECRET)
os.environ.setdefault("AUTOESCROW_SIWE_DOMAIN", "localhost:8000")
os.environ.setdefault("AUTOESCROW_SIWE_URI", "http://localhost:8000")
os.environ.setdefault("AUTOESCROW_LOG_LEVEL", "WARNING")
os.environ.setdefault("AUTOESCROW_LOG_JSON", "false")
os.environ.setdefault("AUTOESCROW_RATE_LIMIT_NONCE_REQUESTS", "10000")
os.environ.setdefault("AUTOESCROW_RATE_LIMIT_VERIFY_REQUESTS", "10000")
os.environ.setdefault("AUTOESCROW_RATE_LIMIT_REFRESH_REQUESTS", "10000")
os.environ.setdefault("AUTOESCROW_RATE_LIMIT_SESSION_REQUESTS", "10000")
os.environ.setdefault("AUTOESCROW_RATE_LIMIT_WALLET_REQUESTS", "10000")

from autoescrow.core.config import Settings, get_settings  # noqa: E402
from autoescrow.infrastructure.database import Database  # noqa: E402
from autoescrow.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def apply_migrations() -> None:
    settings = get_settings()
    database_name = settings.database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if settings.environment not in {"local", "test"} or not database_name.endswith("_test"):
        raise RuntimeError(
            "The test suite requires AUTOESCROW_ENVIRONMENT=local|test "
            "and a database name ending with '_test'"
        )

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def test_settings(apply_migrations: None) -> Settings:
    return Settings()


@pytest.fixture
async def database(test_settings: Settings) -> AsyncIterator[Database]:
    db = Database(test_settings)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture(autouse=True)
async def clean_state(apply_migrations: None, test_settings: Settings) -> AsyncIterator[None]:
    dsn = test_settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "TRUNCATE TABLE ledger_entries, ledger_transactions, escrows, ledger_accounts, "
            "agreement_acceptances, agreement_versions, agreements, jobs, agents, audit_events, "
            "auth_nonces, refresh_sessions, user_roles, wallets, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await connection.close()

    redis = Redis.from_url(test_settings.redis_url, decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()
    yield


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    return create_app(test_settings)


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
