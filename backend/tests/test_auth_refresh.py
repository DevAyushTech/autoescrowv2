import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.infrastructure.database import Database
from autoescrow.models.enums import RefreshRevocationReason
from autoescrow.models.identity import RefreshSession, User
from autoescrow.services.auth_sessions import rotate_refresh_token
from tests.helpers import (
    API,
    audit_events,
    bearer,
    login,
    make_wallet,
    refresh,
    set_user_status,
    user_id_for_wallet,
)


def test_refresh_rotates_the_token_and_invalidates_the_previous_session(
    client: TestClient,
) -> None:
    wallet = make_wallet(41)
    session = login(client, wallet)

    response = refresh(client, session.refresh_token)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    rotated = response.json()
    assert rotated["refresh_token"] != session.refresh_token
    assert rotated["access_token"] != session.access_token
    assert rotated["token_type"] == "bearer"
    assert client.get(f"{API}/users/me", headers=bearer(session.access_token)).status_code == 401
    assert client.get(f"{API}/users/me", headers=bearer(rotated["access_token"])).status_code == 200


def test_refresh_reuse_revokes_the_entire_session_family(client: TestClient) -> None:
    wallet = make_wallet(42)
    session = login(client, wallet)
    rotated = refresh(client, session.refresh_token).json()

    reuse = refresh(client, session.refresh_token)

    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "invalid_refresh_token"
    assert refresh(client, rotated["refresh_token"]).status_code == 401
    assert client.get(f"{API}/users/me", headers=bearer(rotated["access_token"])).status_code == 401


async def test_refresh_rotation_stores_only_a_hash_and_records_lineage(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(43)
    session = login(client, wallet)

    rotated = refresh(client, session.refresh_token).json()

    async with database.session() as db:
        rows = list(
            await db.scalars(select(RefreshSession).order_by(RefreshSession.created_at.asc()))
        )
    assert len(rows) == 2
    old_hash = hashlib.sha256(session.refresh_token.encode()).hexdigest()
    new_hash = hashlib.sha256(rotated["refresh_token"].encode()).hexdigest()
    old = next(row for row in rows if row.token_hash == old_hash)
    new = next(row for row in rows if row.token_hash == new_hash)
    assert old.token_hash != session.refresh_token
    assert old.revoked_at is not None
    assert old.revocation_reason == RefreshRevocationReason.ROTATED.value
    assert old.replaced_by_session_id == new.id
    assert old.family_id == new.family_id


async def test_refresh_reuse_is_audited(client: TestClient, database: Database) -> None:
    wallet = make_wallet(44)
    session = login(client, wallet)
    refresh(client, session.refresh_token)

    assert refresh(client, session.refresh_token).status_code == 401

    events = await audit_events(database, "auth.refresh_reuse_detected")
    assert len(events) == 1
    assert str(events[0]["actor_user_id"]) == session.user["id"]
    assert events[0]["metadata"]["revoked_session_count"] == 1


async def test_unknown_refresh_token_is_rejected(client: TestClient, database: Database) -> None:
    response = refresh(client, "x" * 64)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh_token"
    events = await audit_events(database, "auth.token_refresh_failed")
    assert events[-1]["metadata"]["reason"] == "unknown_token"


def test_logout_revokes_the_current_family(client: TestClient) -> None:
    wallet = make_wallet(45)
    session = login(client, wallet)

    response = client.post(f"{API}/auth/logout", headers=session.headers)

    assert response.status_code == 204
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(f"{API}/users/me", headers=session.headers).status_code == 401
    assert refresh(client, session.refresh_token).status_code == 401


def test_logout_requires_authentication(client: TestClient) -> None:
    response = client.post(f"{API}/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_sessions_endpoint_marks_the_current_session(client: TestClient) -> None:
    wallet = make_wallet(46)
    first = login(client, wallet)
    second = login(client, wallet)
    rotated = refresh(client, second.refresh_token).json()

    response = client.get(
        f"{API}/auth/sessions",
        headers=bearer(rotated["access_token"]),
    )

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert sum(1 for item in sessions if item["current"]) == 1
    assert all(item["expires_at"] > item["created_at"] for item in sessions)
    assert client.get(f"{API}/users/me", headers=first.headers).status_code == 200


def test_revoke_own_session_invalidates_its_access_token(client: TestClient) -> None:
    wallet = make_wallet(47)
    session = login(client, wallet)
    sessions = client.get(f"{API}/auth/sessions", headers=session.headers).json()["sessions"]
    current_id = next(item["id"] for item in sessions if item["current"])

    response = client.post(
        f"{API}/auth/sessions/{current_id}/revoke",
        headers=session.headers,
    )

    assert response.status_code == 204
    assert client.get(f"{API}/users/me", headers=session.headers).status_code == 401
    assert refresh(client, session.refresh_token).status_code == 401


def test_revoke_another_users_session_is_not_found(client: TestClient) -> None:
    first = login(client, make_wallet(48))
    second = login(client, make_wallet(49))
    second_sessions = client.get(f"{API}/auth/sessions", headers=second.headers).json()["sessions"]
    second_session_id = next(item["id"] for item in second_sessions if item["current"])

    response = client.post(
        f"{API}/auth/sessions/{second_session_id}/revoke",
        headers=first.headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert client.get(f"{API}/users/me", headers=second.headers).status_code == 200


def test_revoking_an_unknown_session_returns_not_found(client: TestClient) -> None:
    session = login(client, make_wallet(50))

    response = client.post(
        f"{API}/auth/sessions/00000000-0000-4000-8000-000000000999/revoke",
        headers=session.headers,
    )

    assert response.status_code == 404


async def test_refresh_after_logout_does_not_issue_new_tokens(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(51)
    session = login(client, wallet)
    user_id = await user_id_for_wallet(database, wallet.address)

    client.post(f"{API}/auth/logout", headers=session.headers)
    response = refresh(client, session.refresh_token)

    assert response.status_code == 401
    async with database.session() as db:
        rows = list(
            await db.scalars(select(RefreshSession).where(RefreshSession.user_id == user_id))
        )
    assert all(row.revocation_reason == RefreshRevocationReason.LOGOUT.value for row in rows)
    reuse_events = await audit_events(database, "auth.refresh_reuse_detected")
    assert len(reuse_events) == 1
    assert reuse_events[0]["metadata"]["revoked_session_count"] == 0


async def test_inactive_user_cannot_authenticate_or_refresh(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(52)
    session = login(client, wallet)
    user_id = await user_id_for_wallet(database, wallet.address)
    await set_user_status(database, user_id, "suspended")

    assert client.get(f"{API}/users/me", headers=session.headers).status_code == 403
    response = refresh(client, session.refresh_token)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_unavailable"
    assert client.get(f"{API}/users/me", headers=session.headers).status_code == 401
    events = await audit_events(database, "auth.token_refresh_failed")
    assert events[-1]["metadata"]["reason"] == "account_unavailable"


async def test_expired_refresh_token_is_rejected(
    client: TestClient, database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(53)
    session = login(client, wallet)
    future = datetime.now(UTC) + timedelta(seconds=test_settings.refresh_token_ttl_seconds + 1)

    async with database.session() as db:
        with pytest.raises(ApiError) as error:
            await rotate_refresh_token(
                db,
                test_settings,
                raw_refresh_token=session.refresh_token,
                now=future,
            )

    assert error.value.status_code == 401
    assert error.value.code == "invalid_refresh_token"
    events = await audit_events(database, "auth.token_refresh_failed")
    assert events[-1]["metadata"]["reason"] == "expired"


async def test_access_token_is_rejected_when_the_session_expires(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(54)
    session = login(client, wallet)
    user_id = await user_id_for_wallet(database, wallet.address)

    async with database.session() as db:
        await db.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id)
            .values(
                created_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await db.commit()

    response = client.get(f"{API}/users/me", headers=session.headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"


async def test_concurrent_refresh_allows_one_rotation_and_revokes_the_winner(
    client: TestClient, database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(55)
    session = login(client, wallet)
    issued_at = datetime.now(UTC)

    async def attempt() -> Any:
        async with database.session() as db:
            try:
                return await rotate_refresh_token(
                    db,
                    test_settings,
                    raw_refresh_token=session.refresh_token,
                    now=issued_at,
                )
            except ApiError as exc:
                return exc

    results = await asyncio.gather(attempt(), attempt())
    successes = [item for item in results if not isinstance(item, ApiError)]
    failures = [item for item in results if isinstance(item, ApiError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "invalid_refresh_token"
    assert refresh(client, successes[0].refresh_token).status_code == 401


async def test_refresh_revokes_family_but_not_other_login_families(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(56)
    first = login(client, wallet)
    second = login(client, wallet)
    refresh(client, first.refresh_token)

    assert refresh(client, first.refresh_token).status_code == 401
    assert client.get(f"{API}/users/me", headers=second.headers).status_code == 200
    assert refresh(client, second.refresh_token).status_code == 200

    async with database.session() as db:
        families = await db.scalar(
            select(func.count(func.distinct(RefreshSession.family_id))).select_from(RefreshSession)
        )
        users = await db.scalar(select(func.count()).select_from(User))
    assert families == 2
    assert users == 1
