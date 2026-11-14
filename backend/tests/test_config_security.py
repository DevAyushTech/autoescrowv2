import pytest

from autoescrow.core.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://user:pass@db.example.com/app",
        "redis_url": "rediss://redis.example.com:6379/0",
        "jwt_secret": "production-test-secret-with-at-least-32-bytes",
        "siwe_domain": "api.example.com",
        "siwe_uri": "https://api.example.com",
        "cors_allowed_origins": ["https://app.example.com"],
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_production_settings_are_accepted() -> None:
    settings = production_settings()
    assert settings.environment == "production"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"debug": True}, "debug must be false"),
        ({"sqlalchemy_echo": True}, "sqlalchemy_echo must be false"),
        ({"log_level": "DEBUG"}, "log_level DEBUG is not allowed"),
        ({"siwe_uri": "http://api.example.com"}, "siwe_uri must use HTTPS"),
        ({"siwe_domain": "localhost", "siwe_uri": "https://localhost"}, "public hostname"),
        ({"cors_allowed_origins": ["http://app.example.com"]}, "CORS origins must use HTTPS"),
    ],
)
def test_insecure_production_settings_are_rejected(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        production_settings(**overrides)
