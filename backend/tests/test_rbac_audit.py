import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission, has_permission, permissions_for_roles
from autoescrow.infrastructure.database import Database
from autoescrow.models.audit import AuditEvent
from autoescrow.models.enums import RoleCode
from autoescrow.services.roles import assign_role, revoke_role
from autoescrow.services.tokens import create_access_token, decode_access_token
from tests.helpers import (
    API,
    audit_events,
    bearer,
    link_wallet,
    login,
    make_wallet,
    refresh,
    remove_all_roles,
    user_id_for_wallet,
)
from tests.helpers import (
    assign_role as assign_role_row,
)


def test_role_permission_mapping() -> None:
    base = permissions_for_roles({"USER"})
    assert Permission.PROFILE_READ in base
    assert Permission.AUDIT_READ not in base
    assert permissions_for_roles(set()) == frozenset()
    assert permissions_for_roles({"UNKNOWN"}) == frozenset()
    assert Permission.AUDIT_READ in permissions_for_roles({"ARBITRATOR"})
    assert Permission.ROLE_ASSIGN in permissions_for_roles({"ADMIN"})
    assert has_permission({"ADMIN"}, Permission.ROLE_ASSIGN)
    assert not has_permission({"USER"}, Permission.ROLE_ASSIGN)


async def test_roles_are_reloaded_from_the_database_on_every_request(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(101)
    session = login(client, wallet)
    user_id = await user_id_for_wallet(database, wallet.address)

    await remove_all_roles(database, user_id)
    denied = client.get(f"{API}/users/me", headers=session.headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    await assign_role_row(database, user_id, "ADMIN")
    allowed = client.get(f"{API}/users/me", headers=session.headers)
    assert allowed.status_code == 200
    assert allowed.json()["roles"] == ["ADMIN"]


async def test_roles_claim_in_an_access_token_is_not_trusted(
    client: TestClient, database: Database, test_settings: Settings
) -> None:
    wallet = make_wallet(102)
    session = login(client, wallet)
    user_id = await user_id_for_wallet(database, wallet.address)
    claims = decode_access_token(test_settings, session.access_token)
    await remove_all_roles(database, user_id)

    forged_roles_token = create_access_token(
        test_settings,
        user_id=user_id,
        session_id=claims.session_id,
        roles={"ADMIN"},
    ).value

    response = client.get(f"{API}/users/me", headers=bearer(forged_roles_token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_role_assignment_service_requires_admin_and_is_idempotent(
    client: TestClient, database: Database
) -> None:
    target_wallet = make_wallet(103)
    login(client, target_wallet)
    target_id = await user_id_for_wallet(database, target_wallet.address)
    actor_id = target_id

    async with database.session() as session:
        with pytest.raises(ApiError) as error:
            await assign_role(
                session,
                actor_user_id=actor_id,
                actor_roles={"USER"},
                target_user_id=target_id,
                role_code=RoleCode.ADMIN,
            )
    assert error.value.status_code == 403

    async with database.session() as session:
        assert (
            await assign_role(
                session,
                actor_user_id=actor_id,
                actor_roles={"ADMIN"},
                target_user_id=target_id,
                role_code=RoleCode.ADMIN,
            )
            is True
        )
    async with database.session() as session:
        assert (
            await assign_role(
                session,
                actor_user_id=actor_id,
                actor_roles={"ADMIN"},
                target_user_id=target_id,
                role_code=RoleCode.ADMIN,
            )
            is False
        )


async def test_last_admin_role_cannot_be_revoked(client: TestClient, database: Database) -> None:
    target_wallet = make_wallet(104)
    login(client, target_wallet)
    target_id = await user_id_for_wallet(database, target_wallet.address)
    actor_id = target_id

    async with database.session() as session:
        await assign_role(
            session,
            actor_user_id=actor_id,
            actor_roles={"ADMIN"},
            target_user_id=target_id,
            role_code=RoleCode.ADMIN,
        )
    async with database.session() as session:
        with pytest.raises(ApiError) as error:
            await revoke_role(
                session,
                actor_user_id=actor_id,
                actor_roles={"ADMIN"},
                target_user_id=target_id,
                role_code=RoleCode.ADMIN,
            )
    assert error.value.status_code == 409
    assert error.value.code == "last_admin"


async def test_login_wallet_link_refresh_and_logout_are_audited(
    client: TestClient, database: Database
) -> None:
    owner = make_wallet(105)
    second = make_wallet(106)
    session = login(client, owner)
    link_wallet(client, session, second)
    rotated = refresh(client, session.refresh_token).json()
    client.post(f"{API}/auth/logout", headers=bearer(rotated["access_token"]))

    event_types = [event["event_type"] for event in await audit_events(database)]

    assert "auth.nonce_issued" in event_types
    assert "auth.signature_verified" in event_types
    assert "auth.login_succeeded" in event_types
    assert "wallet.linked" in event_types
    assert "auth.token_refreshed" in event_types
    assert "auth.logout" in event_types


async def test_audit_rows_cannot_be_updated_or_deleted(
    client: TestClient, database: Database
) -> None:
    login(client, make_wallet(107))

    async with database.session() as session:
        event = await session.scalar(select(AuditEvent).limit(1))
        assert event is not None
        event_id = event.id

    async with database.session() as session:
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE id = :id"),
                {"id": event_id},
            )
        await session.rollback()

    async with database.session() as session:
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event_id},
            )
        await session.rollback()

    async with database.session() as session:
        event = await session.get(AuditEvent, event_id)
        assert event is not None
        assert event.event_type != "tampered"


async def test_audit_metadata_excludes_tokens_signatures_and_siwe_messages(
    client: TestClient, database: Database
) -> None:
    wallet = make_wallet(108)
    session = login(client, wallet)
    rotated = refresh(client, session.refresh_token).json()
    refresh(client, session.refresh_token)

    async with database.session() as db:
        events = list(await db.scalars(select(AuditEvent)))

    serialized = json.dumps([event.event_metadata for event in events])
    for secret in (
        session.access_token,
        session.refresh_token,
        rotated["access_token"],
        rotated["refresh_token"],
    ):
        assert secret not in serialized
    assert "signature" not in serialized.lower()
    assert "wants you to sign in" not in serialized.lower()
