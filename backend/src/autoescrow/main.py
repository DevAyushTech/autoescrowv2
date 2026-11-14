import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from autoescrow import __version__
from autoescrow.api.health import router as health_router
from autoescrow.api.router import api_router
from autoescrow.core.config import Settings, get_settings
from autoescrow.core.errors import install_exception_handlers
from autoescrow.core.logging import configure_logging
from autoescrow.core.middleware import RequestContextMiddleware
from autoescrow.infrastructure.database import Database
from autoescrow.infrastructure.rate_limit import RateLimiter
from autoescrow.infrastructure.redis import RedisClient

logger = logging.getLogger("autoescrow")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.database = Database(settings)
    app.state.redis = RedisClient(settings)
    app.state.rate_limiter = RateLimiter(app.state.redis.client, settings)
    logger.info(
        "application.started",
        extra={"environment": settings.environment, "version": __version__},
    )
    try:
        yield
    finally:
        try:
            await app.state.redis.close()
        finally:
            await app.state.database.dispose()
        logger.info("application.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level, json_logs=resolved_settings.log_json)

    is_production = resolved_settings.environment == "production"
    app = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.debug,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(RequestContextMiddleware, production=is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    install_exception_handlers(app)
    # Stable, unversioned probes for load balancers and orchestrators.
    app.include_router(health_router, include_in_schema=False)
    app.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    return app


app = create_app()
