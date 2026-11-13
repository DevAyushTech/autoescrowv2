from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EscrowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agreement_id: UUID


class EscrowActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=16, max_length=128)


class DemoCreditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    idempotency_key: str = Field(min_length=16, max_length=128)


class EscrowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    agreement_id: UUID
    buyer_user_id: UUID
    agent_owner_user_id: UUID
    amount: Decimal
    currency: str = "VCR"
    currency_label: str = "SIMULATED VIRTUAL CREDITS — NO REAL VALUE"
    status: str
    created_at: datetime
    updated_at: datetime


class EscrowListResponse(BaseModel):
    items: list[EscrowResponse]
    total: int
    limit: int
    offset: int


class LedgerBalanceResponse(BaseModel):
    currency: str = "VCR"
    label: str = "SIMULATED VIRTUAL CREDITS — NO REAL VALUE"
    available: Decimal
    escrow_locked: Decimal


class LedgerTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    amount: Decimal
    currency: str
    description: str
    created_at: datetime


class LedgerHistoryResponse(BaseModel):
    items: list[LedgerTransactionResponse]
    total: int
    limit: int
    offset: int
