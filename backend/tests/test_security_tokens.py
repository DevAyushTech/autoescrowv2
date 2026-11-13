import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest

from autoescrow.core.config import Settings
from autoescrow.services.auth_sessions import generate_refresh_token, hash_refresh_token
from autoescrow.services.tokens import (
    AccessTokenError,
    create_access_token,
    decode_access_token,
)


def _base_payload(settings: Settings, *, user_id: str, session_id: str) -> dict[str, Any]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": user_id,
        "sid": session_id,
        "jti": str(uuid4()),
        "type": "access",
        "roles": ["USER"],
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }


def test_access_token_round_trip_contains_required_claims(test_settings: Settings) -> None:
    user_id = uuid4()
    session_id = uuid4()

    token = create_access_token(
        test_settings,
        user_id=user_id,
        session_id=session_id,
        roles={"USER", "AGENT_OWNER"},
    )
    claims = decode_access_token(test_settings, token.value)

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.token_id
    assert claims.roles == frozenset({"USER", "AGENT_OWNER"})
    assert claims.expires_at > claims.issued_at


def test_access_token_rejects_expired_token(test_settings: Settings) -> None:
    issued_in_the_past = datetime.now(UTC) - timedelta(hours=2)
    token = create_access_token(
        test_settings,
        user_id=uuid4(),
        session_id=uuid4(),
        roles={"USER"},
        now=issued_in_the_past,
    )

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token.value)


def test_access_token_rejects_wrong_audience(test_settings: Settings) -> None:
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    payload["aud"] = "another-service"
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token)


def test_access_token_rejects_wrong_issuer(test_settings: Settings) -> None:
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    payload["iss"] = "another-issuer"
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token)


def test_access_token_rejects_unsigned_algorithm(test_settings: Settings) -> None:
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    token = jwt.encode(payload, key="", algorithm="none")

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token)


def test_access_token_rejects_tampered_signature(test_settings: Settings) -> None:
    token = create_access_token(
        test_settings,
        user_id=uuid4(),
        session_id=uuid4(),
        roles={"USER"},
    ).value
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-1]}{'A' if signature[-1] != 'A' else 'B'}"

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, tampered)


@pytest.mark.parametrize(
    "missing_claim",
    ["exp", "iat", "nbf", "iss", "aud", "sub", "sid", "jti", "type", "roles"],
)
def test_access_token_requires_every_claim(test_settings: Settings, missing_claim: str) -> None:
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    payload.pop(missing_claim)
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token)


def test_access_token_rejects_refresh_token_type(test_settings: Settings) -> None:
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    payload["type"] = "refresh"
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError, match="token type"):
        decode_access_token(test_settings, token)


def test_access_token_rejects_non_uuid_identifiers(test_settings: Settings) -> None:
    payload = _base_payload(test_settings, user_id="not-a-uuid", session_id=str(uuid4()))
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError, match="identifiers"):
        decode_access_token(test_settings, token)


def test_access_token_rejects_token_issued_in_the_future(test_settings: Settings) -> None:
    now = int(datetime.now(UTC).timestamp())
    payload = _base_payload(test_settings, user_id=str(uuid4()), session_id=str(uuid4()))
    payload["iat"] = now + 3600
    payload["nbf"] = now - 1
    payload["exp"] = now + 7200
    token = jwt.encode(
        payload,
        test_settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(AccessTokenError):
        decode_access_token(test_settings, token)


def test_refresh_tokens_are_opaque_and_stored_only_as_hashes() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()

    assert first != second
    assert len(first) >= 64
    assert first != hash_refresh_token(first)
    assert hash_refresh_token(first) == hashlib.sha256(first.encode()).hexdigest()
    assert len(hash_refresh_token(first)) == 64
