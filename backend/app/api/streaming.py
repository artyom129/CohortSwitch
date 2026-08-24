import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from redis.exceptions import RedisError
from starlette.responses import StreamingResponse

from backend.app.api.dependencies import Principal, get_principal, require_api_scope
from backend.app.core.errors import APIError
from backend.app.core.redis import redis_client

router = APIRouter(prefix="/api/v1/stream", tags=["streaming"])


def _sse(event: str, data: dict[str, object]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


@router.get("/config")
async def stream_config(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    require_api_scope(principal, "flags:read")
    if not principal.project_id or not principal.environment_id:
        raise APIError(403, "invalid_key_scope", "API key is not scoped to an environment.")
    channel = f"cohortswitch:events:{principal.project_id}:{principal.environment_id}"

    async def events() -> AsyncIterator[bytes]:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(channel)
            yield _sse("connected", {"channel": "config", "retry_ms": 3000})
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message:
                    raw = message["data"]
                    data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                    yield _sse(str(data.pop("event", "flag.updated")), data)
                else:
                    yield b": heartbeat\n\n"
        except RedisError:
            yield _sse("degraded", {"action": "refetch_config_snapshot"})
        finally:
            await pubsub.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
