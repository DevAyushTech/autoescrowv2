from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from autoescrow.core.config import Settings
from autoescrow.infrastructure.database import Database

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_database_ping_and_session_query(test_settings: Settings) -> None:
    database = Database(test_settings)
    try:
        await database.ping()
        async with database.session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_migrations_are_applied_to_head(test_settings: Settings) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None

    database = Database(test_settings)
    try:
        async with database.engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await database.dispose()

    assert version == expected_head
