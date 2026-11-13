import asyncio
import logging
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from autoescrow import __version__
from autoescrow.core.config import Environment, Settings

logger = logging.getLogger("autoescrow.health")

CheckStatus = Literal["ok", "error"]
ReadinessStatus = Literal["ready", "not_ready"]

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: Environment


class ReadinessChecks(BaseModel):
    database: CheckStatus
    redis: CheckStatus


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    checks: ReadinessChecks


async def _probe(component: str, operation: Awaitable[None], timeout_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await operation
    except Exception:
        logger.warning("dependency_check.failed", exc_info=True, extra={"component": component})
        return "error"
    return "ok"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    summary="Readiness probe",
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    database = request.app.state.database
    redis_client = request.app.state.redis

    database_status, redis_status = await asyncio.gather(
        _probe("database", database.ping(), settings.healthcheck_timeout),
        _probe("redis", redis_client.ping(), settings.healthcheck_timeout),
    )

    checks = ReadinessChecks(database=database_status, redis=redis_status)
    if checks.database == "error" or checks.redis == "error":
        response.status_code = 503
        return ReadinessResponse(status="not_ready", checks=checks)

    return ReadinessResponse(status="ready", checks=checks)
