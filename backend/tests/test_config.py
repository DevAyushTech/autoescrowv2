import pytest
from pydantic import ValidationError

from autoescrow.core.config import Settings


def test_settings_normalize_environment_and_log_level() -> None:
    settings = Settings(
        _env_file=None,
        environment="TEST",
        log_level="debug",
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_settings_reject_synchronous_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql://user:pass@localhost:5432/db",
            redis_url="redis://localhost:6379/0",
        )


def test_settings_require_database_name() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:pass@localhost:5432/",
            redis_url="redis://localhost:6379/0",
        )


def test_settings_reject_invalid_redis_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            redis_url="http://localhost:6379/0",
        )


def test_settings_reject_invalid_api_prefix() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            api_v1_prefix="/api/v1/",
            database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            redis_url="redis://localhost:6379/0",
        )


def _security_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+asyncpg://user:pass@localhost:5432/db",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret": "a" * 32,
        "siwe_domain": "localhost:8000",
        "siwe_uri": "http://localhost:8000",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_security_defaults_are_safe() -> None:
    settings = _security_settings()

    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_issuer == "autoescrow-api"
    assert settings.jwt_audience == "autoescrow-clients"
    assert settings.ethereum_chain_id == 11155111
    assert 300 <= settings.access_token_ttl_seconds <= 3600
    assert settings.cors_allowed_origins
    assert "https://autoescrowfrontend.vercel.app" in settings.cors_allowed_origins


def test_settings_reject_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        _security_settings(jwt_secret="too-short")


@pytest.mark.parametrize("chain_id", [1, 8453, 137])
def test_settings_reject_unsupported_chains(chain_id: int) -> None:
    with pytest.raises(ValidationError, match="supported chain"):
        _security_settings(ethereum_chain_id=chain_id)


def test_settings_require_siwe_domain_and_uri_to_match() -> None:
    with pytest.raises(ValidationError, match="must match siwe_domain"):
        _security_settings(siwe_domain="localhost:8000", siwe_uri="http://localhost:3000")


@pytest.mark.parametrize(
    "domain",
    ["http://localhost:8000", "localhost:8000/path", "local host", ""],
)
def test_settings_reject_invalid_siwe_domain(domain: str) -> None:
    with pytest.raises(ValidationError):
        _security_settings(siwe_domain=domain, siwe_uri=f"http://{domain}")


def test_settings_reject_siwe_uri_credentials() -> None:
    with pytest.raises(ValidationError, match="credentials"):
        _security_settings(siwe_uri="http://user:pass@localhost:8000")


def test_settings_reject_cors_origins_with_paths_or_duplicates() -> None:
    with pytest.raises(ValidationError, match="CORS origins"):
        _security_settings(cors_allowed_origins=["http://localhost:3000/app"])

    with pytest.raises(ValidationError, match="duplicates"):
        _security_settings(
            cors_allowed_origins=[
                "http://localhost:3000",
                "http://LOCALHOST:3000",
            ]
        )
