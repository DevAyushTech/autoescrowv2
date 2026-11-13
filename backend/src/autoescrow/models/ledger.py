from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from autoescrow.infrastructure.database import Base
from autoescrow.models.identity import utc_now


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "account_type", name="uq_ledger_account_owner_type"),
        CheckConstraint(
            "account_type IN ('available', 'escrow', 'system')", name="account_type_valid"
        ),
        CheckConstraint("balance >= 0", name="balance_nonnegative"),
        CheckConstraint("currency = 'VCR'", name="virtual_currency_only"),
        Index("ix_ledger_accounts_owner", "owner_user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    account_type: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="VCR", server_default="VCR")
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), default=Decimal("0"), server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_transactions_idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency = 'VCR'", name="virtual_currency_only"),
        CheckConstraint(
            "kind IN ('demo_credit', 'escrow_fund', 'escrow_release', 'escrow_refund')",
            name="kind_valid",
        ),
        Index("ix_ledger_transactions_actor_created", "actor_user_id", "created_at"),
        Index("ix_ledger_transactions_escrow", "escrow_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VCR", server_default="VCR")
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    escrow_id: Mapped[UUID | None] = mapped_column(ForeignKey("escrows.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("side IN ('debit', 'credit')", name="side_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency = 'VCR'", name="virtual_currency_only"),
        Index("ix_ledger_entries_transaction", "transaction_id"),
        Index("ix_ledger_entries_account", "account_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID] = mapped_column(ForeignKey("ledger_accounts.id", ondelete="RESTRICT"))
    side: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VCR", server_default="VCR")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class Escrow(Base):
    __tablename__ = "escrows"
    __table_args__ = (
        UniqueConstraint("agreement_id", name="uq_escrows_agreement_id"),
        CheckConstraint(
            "status IN ('created', 'funded', 'released', 'refunded', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency = 'VCR'", name="virtual_currency_only"),
        Index("ix_escrows_buyer_created", "buyer_user_id", "created_at"),
        Index("ix_escrows_agent_owner_created", "agent_owner_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agreement_id: Mapped[UUID] = mapped_column(ForeignKey("agreements.id", ondelete="RESTRICT"))
    buyer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    agent_owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    escrow_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VCR", server_default="VCR")
    status: Mapped[str] = mapped_column(String(20), default="created", server_default="created")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), onupdate=utc_now
    )
