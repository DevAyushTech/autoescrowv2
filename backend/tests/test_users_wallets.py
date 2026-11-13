import pytest
from fastapi.testclient import TestClient

from autoescrow.infrastructure.database import Database
from tests.helpers import (
    API,
    audit_events,
    link_wallet,
    login,
    make_wallet,
)


def test_profile_requires_authentication(client: TestClient) -> None:
    response = client.get(f"{API}/users/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_profile_returns_identity_and_roles(client: TestClient) -> None:
    wallet = make_wallet(61)
    session = login(client, wallet)

    response = client.get(f"{API}/users/me", headers=session.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session.user["id"]
    assert body["status"] == "active"
    assert body["roles"] == ["USER"]
    assert body["display_name"].startswith("User 0x")
    assert body["email"] is None


def test_garbage_bearer_token_is_rejected(client: TestClient) -> None:
    response = client.get(
        f"{API}/users/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"


def test_basic_scheme_is_rejected(client: TestClient) -> None:
    session = login(client, make_wallet(62))

    response = client.get(
        f"{API}/users/me",
        headers={"Authorization": f"Basic {session.access_token}"},
    )

    assert response.status_code == 401


def test_profile_update_changes_display_name_and_normalizes_email(client: TestClient) -> None:
    session = login(client, make_wallet(63))

    response = client.patch(
        f"{API}/users/me",
        json={"display_name": "Ada Lovelace", "email": "ADA@Example.COM"},
        headers=session.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Ada Lovelace"
    assert body["email"] == "ada@example.com"

    reread = client.get(f"{API}/users/me", headers=session.headers).json()
    assert reread["display_name"] == "Ada Lovelace"
    assert reread["email"] == "ada@example.com"


def test_profile_email_can_be_cleared(client: TestClient) -> None:
    session = login(client, make_wallet(64))
    client.patch(
        f"{API}/users/me",
        json={"email": "clear-me@example.com"},
        headers=session.headers,
    )

    response = client.patch(
        f"{API}/users/me",
        json={"email": None},
        headers=session.headers,
    )

    assert response.status_code == 200
    assert response.json()["email"] is None


def test_profile_update_rejects_empty_payload(client: TestClient) -> None:
    session = login(client, make_wallet(65))

    response = client.patch(f"{API}/users/me", json={}, headers=session.headers)

    assert response.status_code == 422


def test_profile_update_rejects_unknown_fields(client: TestClient) -> None:
    session = login(client, make_wallet(66))

    response = client.patch(
        f"{API}/users/me",
        json={"roles": ["ADMIN"]},
        headers=session.headers,
    )

    assert response.status_code == 422


def test_profile_update_rejects_null_display_name(client: TestClient) -> None:
    session = login(client, make_wallet(67))

    response = client.patch(
        f"{API}/users/me",
        json={"display_name": None},
        headers=session.headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_display_name"


def test_email_must_be_unique_across_users(client: TestClient) -> None:
    first = login(client, make_wallet(68))
    second = login(client, make_wallet(69))
    assert (
        client.patch(
            f"{API}/users/me",
            json={"email": "shared@example.com"},
            headers=first.headers,
        ).status_code
        == 200
    )

    response = client.patch(
        f"{API}/users/me",
        json={"email": "SHARED@example.com"},
        headers=second.headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_unavailable"
    assert client.get(f"{API}/users/me", headers=second.headers).json()["email"] is None


def test_first_login_creates_one_primary_wallet(client: TestClient) -> None:
    wallet = make_wallet(70)
    session = login(client, wallet)

    response = client.get(f"{API}/users/me/wallets", headers=session.headers)

    assert response.status_code == 200
    wallets = response.json()["wallets"]
    assert len(wallets) == 1
    assert wallets[0]["address"] == wallet.address
    assert wallets[0]["chain_id"] == 11155111
    assert wallets[0]["is_primary"] is True


def test_wallet_list_requires_authentication(client: TestClient) -> None:
    response = client.get(f"{API}/users/me/wallets")

    assert response.status_code == 401


def test_link_wallet_after_signature_verification(client: TestClient) -> None:
    owner = make_wallet(71)
    second = make_wallet(72)
    session = login(client, owner)

    response = link_wallet(client, session, second)

    assert response.status_code == 201
    body = response.json()
    assert body["address"] == second.address
    assert body["chain_id"] == 11155111
    assert body["is_primary"] is False

    wallets = client.get(f"{API}/users/me/wallets", headers=session.headers).json()["wallets"]
    assert len(wallets) == 2
    assert wallets[0]["address"] == owner.address
    assert wallets[0]["is_primary"] is True


def test_link_wallet_requires_authentication(client: TestClient) -> None:
    response = client.post(
        f"{API}/users/me/wallets",
        json={"message": "x", "signature": "00" * 65},
    )

    assert response.status_code == 401


async def test_link_wallet_rejects_a_wallet_already_linked_to_the_same_user(
    client: TestClient,
    database: Database,
) -> None:
    owner = make_wallet(73)
    second = make_wallet(74)
    session = login(client, owner)
    assert link_wallet(client, session, second).status_code == 201
    challenge = client.post(
        f"{API}/auth/wallet/nonce",
        json={
            "address": second.address,
            "chain_id": 11155111,
            "purpose": "wallet_link",
        },
        headers=session.headers,
    ).json()
    signature = second.sign(challenge["message"])

    duplicate = client.post(
        f"{API}/users/me/wallets",
        json={"message": challenge["message"], "signature": signature},
        headers=session.headers,
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "wallet_already_linked"

    replay = client.post(
        f"{API}/users/me/wallets",
        json={"message": challenge["message"], "signature": signature},
        headers=session.headers,
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "nonce_already_used"
    events = await audit_events(database, "wallet.link_rejected")
    assert len(events) == 1
    assert events[0]["metadata"]["reason"] == "already_linked"


def test_link_wallet_rejects_a_wallet_owned_by_another_user(client: TestClient) -> None:
    first_wallet = make_wallet(75)
    second_wallet = make_wallet(76)
    first = login(client, first_wallet)
    assert login(client, second_wallet).user["id"] != first.user["id"]

    response = link_wallet(client, first, second_wallet)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "wallet_already_linked"


def test_link_wallet_rejects_signature_by_another_wallet(client: TestClient) -> None:
    owner = make_wallet(77)
    requested = make_wallet(78)
    attacker = make_wallet(79)
    session = login(client, owner)
    challenge_response = client.post(
        f"{API}/auth/wallet/nonce",
        json={
            "address": requested.address,
            "chain_id": 11155111,
            "purpose": "wallet_link",
        },
        headers=session.headers,
    )
    message = challenge_response.json()["message"]

    response = client.post(
        f"{API}/users/me/wallets",
        json={"message": message, "signature": attacker.sign(message)},
        headers=session.headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


def test_wallet_link_nonce_cannot_be_used_by_another_user(client: TestClient) -> None:
    first = login(client, make_wallet(80))
    second = login(client, make_wallet(81))
    new_wallet = make_wallet(82)
    challenge = client.post(
        f"{API}/auth/wallet/nonce",
        json={
            "address": new_wallet.address,
            "chain_id": 11155111,
            "purpose": "wallet_link",
        },
        headers=first.headers,
    ).json()

    response = client.post(
        f"{API}/users/me/wallets",
        json={
            "message": challenge["message"],
            "signature": new_wallet.sign(challenge["message"]),
        },
        headers=second.headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_nonce"


def test_last_wallet_cannot_be_removed(client: TestClient) -> None:
    wallet = make_wallet(83)
    session = login(client, wallet)
    wallet_id = client.get(f"{API}/users/me/wallets", headers=session.headers).json()["wallets"][0][
        "id"
    ]

    response = client.delete(f"{API}/users/me/wallets/{wallet_id}", headers=session.headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_wallet"


def test_non_primary_wallet_can_be_removed(client: TestClient) -> None:
    owner = make_wallet(84)
    second = make_wallet(85)
    session = login(client, owner)
    linked = link_wallet(client, session, second).json()

    response = client.delete(
        f"{API}/users/me/wallets/{linked['id']}",
        headers=session.headers,
    )

    assert response.status_code == 204
    wallets = client.get(f"{API}/users/me/wallets", headers=session.headers).json()["wallets"]
    assert [wallet["address"] for wallet in wallets] == [owner.address]


def test_removing_primary_wallet_promotes_the_oldest_remaining_wallet(client: TestClient) -> None:
    owner = make_wallet(86)
    second = make_wallet(87)
    third = make_wallet(88)
    session = login(client, owner)
    link_wallet(client, session, second)
    link_wallet(client, session, third)
    wallets = client.get(f"{API}/users/me/wallets", headers=session.headers).json()["wallets"]
    primary = next(wallet for wallet in wallets if wallet["is_primary"])

    response = client.delete(
        f"{API}/users/me/wallets/{primary['id']}",
        headers=session.headers,
    )

    assert response.status_code == 204
    remaining = client.get(f"{API}/users/me/wallets", headers=session.headers).json()["wallets"]
    assert len(remaining) == 2
    assert sum(1 for wallet in remaining if wallet["is_primary"]) == 1
    assert next(wallet for wallet in remaining if wallet["is_primary"])["address"] == second.address


def test_cannot_remove_another_users_wallet(client: TestClient) -> None:
    first = login(client, make_wallet(89))
    second = login(client, make_wallet(90))
    second_wallet_id = client.get(f"{API}/users/me/wallets", headers=second.headers).json()[
        "wallets"
    ][0]["id"]

    response = client.delete(
        f"{API}/users/me/wallets/{second_wallet_id}",
        headers=first.headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "wallet_not_found"
    assert len(client.get(f"{API}/users/me/wallets", headers=second.headers).json()["wallets"]) == 1


@pytest.mark.parametrize(
    "wallet_id",
    [
        "00000000-0000-4000-8000-000000000999",
        "not-a-uuid",
    ],
)
def test_remove_unknown_or_invalid_wallet_returns_an_error(
    client: TestClient, wallet_id: str
) -> None:
    session = login(client, make_wallet(91))

    response = client.delete(f"{API}/users/me/wallets/{wallet_id}", headers=session.headers)

    assert response.status_code in {404, 422}
