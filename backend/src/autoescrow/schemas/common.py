"""Shared API schemas."""

import re
from typing import Any

from pydantic import BaseModel, Field

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalize_tags(values: list[str]) -> list[str]:
    """Normalize matching tags so identical concepts compare deterministically."""

    normalized = sorted({item.strip().lower() for item in values if item.strip()})
    if not normalized:
        raise ValueError("at least one non-blank value is required")
    if any(len(item) > 50 for item in normalized):
        raise ValueError("each value must be at most 50 characters")
    return normalized


def normalize_items(values: list[str]) -> list[str]:
    """Normalize ordered agreement content while removing duplicates and blanks."""

    normalized = sorted({item.strip() for item in values if item.strip()})
    if not normalized:
        raise ValueError("at least one non-blank value is required")
    if any(len(item) > 500 for item in normalized):
        raise ValueError("each value must be at most 500 characters")
    return normalized


def normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized):
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return normalized


class ErrorDetail(BaseModel):
    code: str = Field(examples=["invalid_access_token"])
    message: str = Field(examples=["Access token is invalid."])
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    401: {"model": ErrorResponse, "description": "Authentication failed"},
    403: {"model": ErrorResponse, "description": "Permission denied"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    503: {"model": ErrorResponse, "description": "Security dependency unavailable"},
}
