"""Deterministic helpers for the identity and access test suite."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from fastapi.testclient import TestClient

API = "/api/v1"
CHAIN_ID = 11155111


@dataclass(frozen=True, slots=True)
class TestWallet:
    """A deterministic, throwaway Ethereum account used only in tests."""

    account: LocalAccount

    @property
    def address(self) -> str:
        address: str = self.account.address
        return address

    def sign(self, message: str, *, with_prefix: bool = False) -> str:
        signature = self.account.sign_message(encode_defunct(text=message)).signature.hex()
        return f"0x{signature}" if with_prefix else signature


def make_wallet(seed: int = 1) -> TestWallet:
    if seed <= 0:
        raise ValueError("seed must be positive")
    return TestWallet(Account.from_key(f"0x{seed:064x}"))


@dataclass(frozen=True, slots=True)
class LoginSession:
    access_token: str
    access_token_expires_at: str
    refresh_token: str
    refresh_token_expires_at: str
    user: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class NonceChallenge:
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        value: str = self.raw["message"]
        return value

    @property
    def nonce(self) -> str:
        value: str = self.raw["nonce"]
        return value


def request_nonce(
    client: TestClient,
    wallet: TestWallet,
    *,
    purpose: str = "login",
    chain_id: int = CHAIN_ID,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"{API}/auth/wallet/nonce",
        json={"address": wallet.address, "chain_id": chain_id, "purpose": purpose},
        headers=headers,
    )


def get_nonce(
    client: TestClient,
    wallet: TestWallet,
    *,
    purpose: str = "login",
    chain_id: int = CHAIN_ID,
    headers: dict[str, str] | None = None,
) -> NonceChallenge:
    response = request_nonce(
        client,
        wallet,
        purpose=purpose,
        chain_id=chain_id,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return NonceChallenge(raw=body)


def verify(
    client: TestClient,
    challenge: NonceChallenge,
    wallet: TestWallet,
    *,
    signature: str | None = None,
) -> Any:
    return verify_raw(
        client,
        challenge.message,
        signature if signature is not None else wallet.sign(challenge.message),
    )


def verify_raw(client: TestClient, message: str, signature: str) -> Any:
    return client.post(
        f"{API}/auth/wallet/verify",
        json={"message": message, "signature": signature},
    )


def login(
    client: TestClient,
    wallet: TestWallet,
    *,
    chain_id: int = CHAIN_ID,
) -> LoginSession:
    challenge = get_nonce(client, wallet, chain_id=chain_id)
    response = verify(client, challenge, wallet)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return LoginSession(
        access_token=body["access_token"],
        access_token_expires_at=body["access_token_expires_at"],
        refresh_token=body["refresh_token"],
        refresh_token_expires_at=body["refresh_token_expires_at"],
        user=body["user"],
    )


def link_wallet(client: TestClient, session: LoginSession, wallet: TestWallet) -> Any:
    challenge = get_nonce(
        client,
        wallet,
        purpose="wallet_link",
        headers=session.headers,
    )
    return client.post(
        f"{API}/users/me/wallets",
        json={"message": challenge.message, "signature": wallet.sign(challenge.message)},
        headers=session.headers,
    )


def refresh(client: TestClient, refresh_token: str) -> Any:
    return client.post(
        f"{API}/auth/token/refresh",
        json={"refresh_token": refresh_token},
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def user_id_for_wallet(database: Any, address: str) -> UUID:
    from sqlalchemy import select

    from autoescrow.models.identity import Wallet

    async with database.session() as session:
        wallet = await session.scalar(select(Wallet).where(Wallet.address == address))
        assert wallet is not None
        return UUID(str(wallet.user_id))


async def assign_role(database: Any, user_id: UUID, role_code: str) -> None:
    from sqlalchemy import select

    from autoescrow.models.rbac import Role, UserRole

    async with database.session() as session:
        role = await session.scalar(select(Role).where(Role.code == role_code))
        assert role is not None
        session.add(UserRole(user_id=user_id, role_id=role.id))
        await session.commit()


async def remove_all_roles(database: Any, user_id: UUID) -> None:
    from sqlalchemy import delete

    from autoescrow.models.rbac import UserRole

    async with database.session() as session:
        await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await session.commit()


async def set_user_status(database: Any, user_id: UUID, status: str) -> None:
    from autoescrow.models.identity import User

    async with database.session() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.status = status
        await session.commit()


async def audit_events(database: Any, event_type: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from autoescrow.models.audit import AuditEvent

    async with database.session() as session:
        statement = select(AuditEvent).order_by(AuditEvent.created_at.asc())
        if event_type is not None:
            statement = statement.where(AuditEvent.event_type == event_type)
        events = await session.scalars(statement)
        return [
            {
                "event_type": event.event_type,
                "subject_type": event.subject_type,
                "subject_id": event.subject_id,
                "actor_user_id": event.actor_user_id,
                "metadata": event.event_metadata,
            }
            for event in events
        ]
