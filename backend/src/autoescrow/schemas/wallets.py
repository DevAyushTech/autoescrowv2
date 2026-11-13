from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoescrow.models.identity import Wallet


class WalletResponse(BaseModel):
    id: UUID
    address: str
    chain_id: int
    is_primary: bool
    verified_at: datetime
    created_at: datetime

    @classmethod
    def from_wallet(cls, wallet: Wallet) -> "WalletResponse":
        return cls(
            id=wallet.id,
            address=wallet.address,
            chain_id=wallet.chain_id,
            is_primary=wallet.is_primary,
            verified_at=wallet.verified_at,
            created_at=wallet.created_at,
        )


class WalletListResponse(BaseModel):
    wallets: list[WalletResponse]


class WalletLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4096)
    signature: str = Field(min_length=2, max_length=256)

    @field_validator("message", "signature")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value
