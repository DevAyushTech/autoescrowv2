import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

SUPPORTED_CHAIN_IDS = frozenset({11155111})
_MINIMUM_JWT_SECRET_BYTES = 32
_DOMAIN_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?$")


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="AUTOESCROW_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AutoEscrow API"
    environment: Environment = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str
    redis_url: str

    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_connect_timeout: float = Field(default=3.0, gt=0, le=30)
    sqlalchemy_echo: bool = False

    redis_connect_timeout: float = Field(default=2.0, gt=0, le=30)
    healthcheck_timeout: float = Field(default=5.0, gt=0, le=30)

    log_level: LogLevel = "INFO"
    log_json: bool = True

    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "autoescrow-api"
    jwt_audience: str = "autoescrow-clients"
    access_token_ttl_seconds: int = Field(default=900, ge=300, le=3600)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    auth_nonce_ttl_seconds: int = Field(default=300, ge=30, le=900)
    auth_clock_skew_seconds: int = Field(default=30, ge=0, le=120)

    ethereum_chain_id: int = Field(default=11155111, gt=0)
    siwe_domain: str = "localhost:8000"
    siwe_uri: str = "http://localhost:8000"
    siwe_statement: str = Field(default="Sign in to AutoEscrow.", min_length=1, max_length=200)

    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "https://autoescrowfrontend.vercel.app",
        ]
    )

    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_nonce_requests: int = Field(default=10, ge=1, le=100_000)
    rate_limit_verify_requests: int = Field(default=20, ge=1, le=100_000)
    rate_limit_refresh_requests: int = Field(default=30, ge=1, le=100_000)
    rate_limit_session_requests: int = Field(default=60, ge=1, le=100_000)
    rate_limit_wallet_requests: int = Field(default=30, ge=1, le=100_000)

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_v1_prefix must start with '/' and must not end with '/'")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "postgresql+asyncpg" or not parsed.hostname:
            raise ValueError("database_url must be a postgresql+asyncpg:// URL with a host")
        if not parsed.path.strip("/"):
            raise ValueError("database_url must include a database name")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("redis_url must be a redis:// or rediss:// URL with a host")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret.encode("utf-8")) < _MINIMUM_JWT_SECRET_BYTES:
            raise ValueError(f"jwt_secret must be at least {_MINIMUM_JWT_SECRET_BYTES} bytes")
        return value

    @field_validator("ethereum_chain_id")
    @classmethod
    def validate_supported_chain(cls, value: int) -> int:
        if value not in SUPPORTED_CHAIN_IDS:
            raise ValueError("ethereum_chain_id must be a supported chain (11155111 for Sepolia)")
        return value

    @field_validator("siwe_domain")
    @classmethod
    def validate_siwe_domain(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _DOMAIN_PATTERN.fullmatch(normalized):
            raise ValueError("siwe_domain must be a bare authority such as 'example.com'")
        return normalized

    @field_validator("siwe_uri")
    @classmethod
    def validate_siwe_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("siwe_uri must be an absolute http:// or https:// URI")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("siwe_uri must not contain credentials or a fragment")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("cors_allowed_origins must contain at least one origin")

        normalized: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("each CORS origin must be an absolute http(s) origin")
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("CORS origins must not contain credentials, paths or queries")
            normalized.append(f"{parsed.scheme}://{parsed.netloc}".lower())

        if len(set(normalized)) != len(normalized):
            raise ValueError("cors_allowed_origins must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_siwe_binding(self) -> "Settings":
        uri_authority = urlsplit(self.siwe_uri).netloc.lower()
        if uri_authority != self.siwe_domain:
            raise ValueError("siwe_uri authority must match siwe_domain")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from environment variables
