import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.infrastructure.database import Database
from autoescrow.infrastructure.siwe_library import SiweMessage
from autoescrow.models.enums import AuthNoncePurpose
from autoescrow.models.identity import AuthNonce, RefreshSession, User
from autoescrow.models.ledger import LedgerAccount, LedgerTransaction
from autoescrow.services.authentication import LoginResult, login_with_siwe
from autoescrow.services.siwe import issue_nonce, verify_and_consume_nonce
from tests.helpers import (
    API,
    CHAIN_ID,
    audit_events,
    get_nonce,
    login,
    make_wallet,
    request_nonce,
    verify,
    verify_raw,
)


def test_nonce_returns_a_bound_eip4361_message(client: TestClient) -> None:
    wallet = make_wallet(11)

    response = request_nonce(client, wallet)

    assert response.status_code == 200
    body = response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert body["address"] == wallet.address
    assert body["chain_id"] == CHAIN_ID
    assert body["purpose"] == "login"
    assert len(body["nonce"]) == 32
    assert body["message"].startswith(
        f"localhost:8000 wants you to sign in with your Ethereum account:\n{wallet.address}"
    )
    assert "Sign in to AutoEscrow." in body["message"]
    assert "URI: http://localhost:8000" in body["message"]
    assert f"Chain ID: {CHAIN_ID}" in body["message"]
    assert f"Nonce: {body['nonce']}" in body["message"]
    assert "Expiration Time:" in body["message"]


def test_nonce_normalizes_lowercase_address_to_checksum(client: TestClient) -> None:
    wallet = make_wallet(12)

    response = client.post(
        f"{API}/auth/wallet/nonce",
        json={"address": wallet.address.lower(), "chain_id": CHAIN_ID},
    )

    assert response.status_code == 200
    assert response.json()["address"] == wallet.address


def test_each_nonce_request_generates_a_unique_challenge(client: TestClient) -> None:
    wallet = make_wallet(13)

    first = get_nonce(client, wallet)
    second = get_nonce(client, wallet)

    assert first.nonce != second.nonce
    assert first.message != second.message


@pytest.mark.parametrize(
    "address",
    [
        "not-an-address",
        "0x1234",
        "0x" + "zz" * 20,
    ],
)
def test_nonce_rejects_invalid_addresses(client: TestClient, address: str) -> None:
    response = client.post(
        f"{API}/auth/wallet/nonce",
        json={"address": address, "chain_id": CHAIN_ID},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {
        "validation_error",
        "invalid_wallet_address",
    }


def test_nonce_rejects_unsupported_chain(client: TestClient) -> None:
    wallet = make_wallet(14)

    response = request_nonce(client, wallet, chain_id=1)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_chain"


def test_nonce_rejects_unknown_fields(client: TestClient) -> None:
    wallet = make_wallet(15)

    response = client.post(
        f"{API}/auth/wallet/nonce",
        json={"address": wallet.address, "chain_id": CHAIN_ID, "roles": ["ADMIN"]},
    )

    assert response.status_code == 422


def test_wallet_link_nonce_requires_authentication(client: TestClient) -> None:
    wallet = make_wallet(16)

    response = request_nonce(client, wallet, purpose="wallet_link")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_wallet_link_nonce_is_bound_to_the_authenticated_user(client: TestClient) -> None:
    owner = make_wallet(17)
    session = login(client, owner)
    other = make_wallet(18)

    response = request_nonce(client, other, purpose="wallet_link", headers=session.headers)

    assert response.status_code == 200
    assert response.json()["purpose"] == "wallet_link"
    assert other.address in response.json()["message"]


def test_nonce_is_single_use(client: TestClient) -> None:
    wallet = make_wallet(19)
    challenge = get_nonce(client, wallet)

    first = verify(client, challenge, wallet)
    second = verify(client, challenge, wallet)

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "nonce_already_used"


async def test_replay_does_not_create_a_second_user_or_session(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(20)
    challenge = get_nonce(client, wallet)
    signature = wallet.sign(challenge.message)

    assert verify_raw(client, challenge.message, signature).status_code == 200
    assert verify_raw(client, challenge.message, signature).status_code == 401

    async with database.session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        sessions = await session.scalar(select(func.count()).select_from(RefreshSession))
    assert (users, sessions) == (1, 1)


async def test_new_production_user_receives_one_time_welcome_vcr(
    database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(34)
    production_settings = test_settings.model_copy(update={"environment": "production"})

    async def authenticate_new_wallet() -> LoginResult:
        async with database.session() as session:
            challenge = await issue_nonce(
                session,
                test_settings,
                address=wallet.address,
                chain_id=CHAIN_ID,
                purpose=AuthNoncePurpose.LOGIN,
            )
        async with database.session() as session:
            return await login_with_siwe(
                session,
                production_settings,
                message=challenge.message,
                signature=wallet.sign(challenge.message),
            )

    first = await authenticate_new_wallet()
    second = await authenticate_new_wallet()
    assert first.user.id == second.user.id

    async with database.session() as session:
        account = await session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.owner_user_id == first.user.id,
                LedgerAccount.account_type == "available",
            )
        )
        grants = list(
            await session.scalars(
                select(LedgerTransaction).where(
                    LedgerTransaction.idempotency_key == f"welcome-vcr-v1:{first.user.id}"
                )
            )
        )
    assert account is not None and account.balance == 1000
    assert len(grants) == 1
    assert grants[0].amount == 1000
    assert grants[0].currency == "VCR"


async def test_unknown_nonce_is_rejected(client: TestClient, database: Database) -> None:
    wallet = make_wallet(21)
    challenge = get_nonce(client, wallet)
    forged = challenge.message.replace(challenge.nonce, "0" * 32)
    signature = wallet.sign(forged)

    response = verify_raw(client, forged, signature)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_nonce"

    events = await audit_events(database, "auth.signature_failed")
    assert events[-1]["metadata"]["reason"] == "unknown_nonce"


async def test_malformed_siwe_message_is_rejected(client: TestClient, database: Database) -> None:
    response = verify_raw(client, "this is not a SIWE message", "00" * 65)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_siwe_message"

    events = await audit_events(database, "auth.signature_failed")
    assert events[-1]["metadata"]["reason"] == "malformed_message"


def test_login_nonce_cannot_be_used_for_wallet_linking(client: TestClient) -> None:
    owner = make_wallet(22)
    second = make_wallet(23)
    session = login(client, owner)
    challenge = get_nonce(client, second)

    response = client.post(
        f"{API}/users/me/wallets",
        json={"message": challenge.message, "signature": second.sign(challenge.message)},
        headers=session.headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_nonce"


async def test_expired_nonce_is_rejected(database: Database, test_settings: Settings) -> None:
    wallet = make_wallet(24)
    issued_at = datetime.now(UTC) - timedelta(seconds=test_settings.auth_nonce_ttl_seconds + 60)

    async with database.session() as session:
        challenge = await issue_nonce(
            session,
            test_settings,
            address=wallet.address,
            chain_id=CHAIN_ID,
            purpose=AuthNoncePurpose.LOGIN,
            now=issued_at,
        )
    signature = wallet.sign(challenge.message)

    async with database.session() as session:
        with pytest.raises(ApiError) as error:
            await verify_and_consume_nonce(
                session,
                test_settings,
                message=challenge.message,
                signature=signature,
                purpose=AuthNoncePurpose.LOGIN,
                now=issued_at + timedelta(seconds=test_settings.auth_nonce_ttl_seconds + 1),
            )
    assert error.value.status_code == 401
    assert error.value.code == "nonce_expired"

    async with database.session() as session:
        nonce = await session.scalar(select(AuthNonce).where(AuthNonce.nonce == challenge.nonce))
        assert nonce is not None
        assert nonce.consumed_at is None


async def test_message_issued_at_in_the_future_is_rejected(
    database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(25)
    issued_at = datetime.now(UTC)

    async with database.session() as session:
        challenge = await issue_nonce(
            session,
            test_settings,
            address=wallet.address,
            chain_id=CHAIN_ID,
            purpose=AuthNoncePurpose.LOGIN,
            now=issued_at,
        )
    signature = wallet.sign(challenge.message)

    async with database.session() as session:
        with pytest.raises(ApiError) as error:
            await verify_and_consume_nonce(
                session,
                test_settings,
                message=challenge.message,
                signature=signature,
                purpose=AuthNoncePurpose.LOGIN,
                now=issued_at - timedelta(seconds=test_settings.auth_clock_skew_seconds + 5),
            )
    assert error.value.code == "siwe_issued_at_invalid"


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("domain", "evil.example.com", "siwe_domain_mismatch"),
        ("uri", "https://evil.example.com", "siwe_uri_mismatch"),
        ("chain_id", 1, "siwe_chain_mismatch"),
        ("statement", "Sign in to EvilEscrow.", "siwe_statement_mismatch"),
    ],
)
def test_tampered_message_bindings_are_rejected(
    client: TestClient,
    field: str,
    value: object,
    expected_code: str,
) -> None:
    wallet = make_wallet(26)
    challenge = get_nonce(client, wallet)
    parsed = SiweMessage.from_message(challenge.message)
    setattr(parsed, field, value)
    tampered = parsed.prepare_message()

    response = verify_raw(client, tampered, wallet.sign(tampered))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == expected_code


def test_tampered_wallet_address_is_rejected(client: TestClient) -> None:
    wallet = make_wallet(27)
    other = make_wallet(28)
    challenge = get_nonce(client, wallet)
    parsed = SiweMessage.from_message(challenge.message)
    parsed.address = other.address
    tampered = parsed.prepare_message()

    response = verify_raw(client, tampered, wallet.sign(tampered))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "siwe_wallet_mismatch"


def test_signature_from_another_wallet_is_rejected(client: TestClient) -> None:
    wallet = make_wallet(29)
    attacker = make_wallet(30)
    challenge = get_nonce(client, wallet)

    response = verify(client, challenge, attacker)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


@pytest.mark.parametrize("signature", ["", "0x", "zz" * 65, "00" * 64, "00" * 66])
def test_malformed_signatures_are_rejected(client: TestClient, signature: str) -> None:
    wallet = make_wallet(31)
    challenge = get_nonce(client, wallet)

    response = verify(client, challenge, wallet, signature=signature)

    assert response.status_code in {401, 422}


def test_prefixed_signature_is_accepted(client: TestClient) -> None:
    wallet = make_wallet(32)
    challenge = get_nonce(client, wallet)

    response = verify(
        client, challenge, wallet, signature=wallet.sign(challenge.message, with_prefix=True)
    )

    assert response.status_code == 200


async def test_concurrent_use_of_one_nonce_allows_exactly_one_login(
    database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(33)
    issued_at = datetime.now(UTC)

    async def attempt(challenge_message: str, signature: str) -> LoginResult | ApiError:
        async with database.session() as session:
            try:
                return await login_with_siwe(
                    session,
                    test_settings,
                    message=challenge_message,
                    signature=signature,
                    now=issued_at,
                )
            except ApiError as exc:
                return exc

    async with database.session() as session:
        challenge = await issue_nonce(
            session,
            test_settings,
            address=wallet.address,
            chain_id=CHAIN_ID,
            purpose=AuthNoncePurpose.LOGIN,
            now=issued_at,
        )
    signature = wallet.sign(challenge.message)
    results = await asyncio.gather(
        attempt(challenge.message, signature),
        attempt(challenge.message, signature),
    )

    successes = [item for item in results if isinstance(item, LoginResult)]
    failures = [item for item in results if isinstance(item, ApiError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 401

    async with database.session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        sessions = await session.scalar(select(func.count()).select_from(RefreshSession))
        nonce = await session.scalar(select(AuthNonce).where(AuthNonce.nonce == challenge.nonce))
    assert users == 1
    assert sessions == 1
    assert nonce is not None and nonce.consumed_at is not None
