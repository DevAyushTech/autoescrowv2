from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from autoescrow.infrastructure.database import Database
from autoescrow.models.identity import RefreshSession, User, Wallet
from tests.helpers import (
    API,
    audit_events,
    bearer,
    get_nonce,
    link_wallet,
    login,
    make_wallet,
    refresh,
    verify,
)


async def test_complete_identity_lifecycle(client: TestClient, database: Database) -> None:
    primary = make_wallet(201)
    secondary = make_wallet(202)

    challenge = get_nonce(client, primary)
    verified = verify(client, challenge, primary)
    assert verified.status_code == 200
    body: dict[str, Any] = verified.json()
    access_token = body["access_token"]
    refresh_token = body["refresh_token"]
    original_user_id = body["user"]["id"]
    assert body["user"]["roles"] == ["USER"]

    me = client.get(f"{API}/users/me", headers=bearer(access_token))
    assert me.status_code == 200
    assert me.json()["id"] == original_user_id

    updated = client.patch(
        f"{API}/users/me",
        json={"display_name": "Integration User", "email": "integration@example.com"},
        headers=bearer(access_token),
    )
    assert updated.status_code == 200
    assert updated.json()["email"] == "integration@example.com"

    linked = link_wallet(
        client,
        login(client, primary),
        secondary,
    )
    assert linked.status_code == 201

    wallets = client.get(f"{API}/users/me/wallets", headers=bearer(access_token)).json()["wallets"]
    assert len(wallets) == 2
    assert sum(1 for wallet in wallets if wallet["is_primary"]) == 1

    rotated = refresh(client, refresh_token)
    assert rotated.status_code == 200
    rotated_body = rotated.json()
    assert client.get(f"{API}/users/me", headers=bearer(access_token)).status_code == 401
    assert (
        client.get(f"{API}/users/me", headers=bearer(rotated_body["access_token"])).status_code
        == 200
    )

    assert refresh(client, refresh_token).status_code == 401
    assert refresh(client, rotated_body["refresh_token"]).status_code == 401
    assert (
        client.get(f"{API}/users/me", headers=bearer(rotated_body["access_token"])).status_code
        == 401
    )

    relogin = login(client, primary)
    assert relogin.user["id"] == original_user_id
    assert client.get(f"{API}/users/me", headers=relogin.headers).status_code == 200

    async with database.session() as session:
        user_count = await session.scalar(select(func.count()).select_from(User))
        wallet_count = await session.scalar(select(func.count()).select_from(Wallet))
        refresh_sessions = list(await session.scalars(select(RefreshSession)))

    assert user_count == 1
    assert wallet_count == 2
    assert len(refresh_sessions) == 4
    assert all(len(item.token_hash) == 64 for item in refresh_sessions)
    stored_hashes = {item.token_hash for item in refresh_sessions}
    assert refresh_token not in stored_hashes
    assert rotated_body["refresh_token"] not in stored_hashes
    assert relogin.refresh_token not in stored_hashes

    event_types = {event["event_type"] for event in await audit_events(database)}
    assert "auth.login_succeeded" in event_types
    assert "wallet.linked" in event_types
    assert "auth.token_refreshed" in event_types
    assert "auth.refresh_reuse_detected" in event_types


async def test_returning_wallet_maps_to_the_same_user(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(203)
    first = login(client, wallet)
    client.post(f"{API}/auth/logout", headers=first.headers)

    second = login(client, wallet)

    assert second.user["id"] == first.user["id"]
    assert second.refresh_token != first.refresh_token
    async with database.session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        wallets = await session.scalar(select(func.count()).select_from(Wallet))
    assert users == 1
    assert wallets == 1
    login_events = await audit_events(database, "auth.login_succeeded")
    assert len(login_events) == 2


async def test_two_users_remain_fully_isolated(client: TestClient, database: Database) -> None:
    first = login(client, make_wallet(204))
    second = login(client, make_wallet(205))

    assert first.user["id"] != second.user["id"]
    first_wallets = client.get(f"{API}/users/me/wallets", headers=first.headers).json()["wallets"]
    second_wallets = client.get(f"{API}/users/me/wallets", headers=second.headers).json()["wallets"]
    assert first_wallets[0]["address"] != second_wallets[0]["address"]

    deletion = client.delete(
        f"{API}/users/me/wallets/{second_wallets[0]['id']}",
        headers=first.headers,
    )
    assert deletion.status_code == 404
    assert client.get(f"{API}/users/me", headers=second.headers).status_code == 200

    async with database.session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        wallets = await session.scalar(select(func.count()).select_from(Wallet))
    assert users == 2
    assert wallets == 2
