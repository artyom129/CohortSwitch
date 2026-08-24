import asyncio
import hashlib
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import structlog
from fastapi import Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.app.core.config import Settings
from backend.app.core.metrics import HTTP_DURATION, HTTP_REQUESTS

logger = structlog.get_logger()


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        try:
            request_id = str(UUID(incoming)) if incoming else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id

        content_length = request.headers.get("content-length")
        try:
            too_large = bool(content_length and int(content_length) > self.settings.max_body_bytes)
        except ValueError:
            too_large = True
        if too_large:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request body exceeds the configured limit.",
                        "request_id": request_id,
                    }
                },
                headers={"X-Request-ID": request_id},
            )

        started = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - started
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(duration)
        response.headers["X-Request-ID"] = request_id
        await logger.info(
            "http_request",
            request_id=request_id,
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings, redis: Redis) -> None:
        super().__init__(app)
        self.settings = settings
        self.redis = redis
        self._local: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        self._lock = asyncio.Lock()

    async def _local_allowed(self, key: str, limit: int, window: int) -> bool:
        async with self._lock:
            current_window, count = self._local[key]
            if current_window != window:
                self._local[key] = (window, 1)
                return True
            if count >= limit:
                return False
            self._local[key] = (window, count + 1)
            if len(self._local) > 20_000:
                self._local = {
                    item_key: value
                    for item_key, value in self._local.items()
                    if value[0] >= window - 1
                }
            return True

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith(("/health", "/metrics")):
            return await call_next(request)

        is_evaluation = path.startswith("/api/v1/evaluate")
        bucket = "evaluation" if is_evaluation else "management"
        limit = (
            self.settings.sdk_rate_limit if is_evaluation else self.settings.management_rate_limit
        )
        credential = request.headers.get("X-CohortSwitch-Key")
        identity = credential or (request.client.host if request.client else "unknown")
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()[:24]
        window = int(time.time()) // 60
        key = f"cohortswitch:ratelimit:{bucket}:{identity_hash}:{window}"
        try:
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, 61)
            allowed = count <= limit
        except RedisError as exc:
            allowed = await self._local_allowed(key, limit, window)
            await logger.warning(
                "rate_limit_redis_unavailable", bucket=bucket, error=type(exc).__name__
            )

        if not allowed:
            request_id = getattr(request.state, "request_id", str(uuid4()))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Too many requests.",
                        "request_id": request_id,
                    }
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


__all__ = ["RateLimitMiddleware", "RequestContextMiddleware"]
