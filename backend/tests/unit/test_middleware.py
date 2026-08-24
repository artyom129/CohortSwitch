import httpx
import pytest
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from backend.app.core.config import Settings
from backend.app.core.middleware import RateLimitMiddleware


class UnavailableRedis:
    async def incr(self, key: str) -> int:
        raise RedisConnectionError("offline")


@pytest.mark.asyncio
async def test_rate_limit_uses_local_fallback_when_redis_is_down() -> None:
    test_app = FastAPI()
    test_app.add_middleware(
        RateLimitMiddleware,
        settings=Settings(
            jwt_secret="middleware-test-secret-that-is-longer-than-thirty-two-characters",
            sdk_rate_limit=2,
        ),
        redis=UnavailableRedis(),
    )

    @test_app.get("/api/v1/evaluate")
    async def endpoint() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/evaluate")).status_code == 200
        assert (await client.get("/api/v1/evaluate")).status_code == 200
        assert (await client.get("/api/v1/evaluate")).status_code == 429
