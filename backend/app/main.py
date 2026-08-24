from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.api import auth, evaluation, health, management, streaming
from backend.app.core.config import get_settings
from backend.app.core.errors import (
    APIError,
    api_error_handler,
    integrity_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from backend.app.core.logging import configure_logging
from backend.app.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from backend.app.core.redis import redis_client

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = redis_client
    await logger.info("application_started", environment=settings.app_env)
    yield
    await redis_client.aclose()
    await logger.info("application_stopped")


app = FastAPI(
    title="CohortSwitch API",
    version="0.1.0",
    description="Feature flag evaluation and progressive rollout control plane.",
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "If-None-Match",
        "X-CohortSwitch-Key",
        "X-Request-ID",
    ],
)
app.add_middleware(RateLimitMiddleware, settings=settings, redis=redis_client)
app.add_middleware(RequestContextMiddleware, settings=settings)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(auth.router)
app.include_router(management.router)
app.include_router(evaluation.router)
app.include_router(streaming.router)
app.include_router(health.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": "CohortSwitch", "docs": "/docs", "health": "/health/ready"}
