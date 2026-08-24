from redis.asyncio import Redis

from backend.app.core.config import get_settings

redis_client = Redis.from_url(
    get_settings().redis_url,
    encoding="utf-8",
    decode_responses=False,
    socket_connect_timeout=1,
    socket_timeout=1,
)
