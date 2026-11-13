from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autoescrow.infrastructure.database import Base
from autoescrow.models.enums import AuthNoncePurpose, UserStatus

if TYPE_CHECKING:
    from autoescrow.models.rbac import UserRole


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'deactivated')",
            name="status_valid",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 100",
            name="display_name_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    status: Mapped[str] = mapped_column(
        String(20), default=UserStatus.ACTIVE.value, server_default=UserStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )

    wallets: Mapped[list[Wallet]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
    role_assignments: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id",
        lazy="selectin",
    )


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("chain_id", "address", name="uq_wallets_chain_id_address"),
        Index(
            "uq_wallets_user_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint(
            "address ~ '^0x[0-9a-fA-F]{40}$'",
            name="address_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(42))
    chain_id: Mapped[int]
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="wallets", lazy="raise")


class AuthNonce(Base):
    __tablename__ = "auth_nonces"
    __table_args__ = (
        UniqueConstraint("nonce", name="uq_auth_nonces_nonce"),
        CheckConstraint(
            "purpose IN ('login', 'wallet_link')",
            name="purpose_valid",
        ),
        CheckConstraint(
            "purpose <> 'wallet_link' OR user_id IS NOT NULL",
            name="wallet_link_requires_user",
        ),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="consumption_after_issue",
        ),
        Index(
            "ix_auth_nonces_address_purpose_expires_at",
            "address",
            "purpose",
            "expires_at",
        ),
        Index("ix_auth_nonces_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(42))
    chain_id: Mapped[int]
    nonce: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(20), default=AuthNoncePurpose.LOGIN.value)
    message: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
        UniqueConstraint(
            "replaced_by_session_id", name="uq_refresh_sessions_replaced_by_session_id"
        ),
        CheckConstraint("char_length(token_hash) = 64", name="token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="revocation_after_creation",
        ),
        Index("ix_refresh_sessions_family_id", "family_id"),
        Index("ix_refresh_sessions_user_id_expires_at", "user_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    family_id: Mapped[UUID] = mapped_column(default=uuid4)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(40))
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
