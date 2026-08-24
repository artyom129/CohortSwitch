from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.redis import redis_client
from backend.app.database.session import get_session

router = APIRouter(tags=["operations"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    postgres = "ok"
    redis = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        postgres = "unavailable"
    try:
        await redis_client.ping()
    except RedisError:
        redis = "unavailable"
    if postgres != "ok":
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "postgres": postgres, "redis": redis},
        )
    status = "ok" if redis == "ok" else "degraded"
    return JSONResponse(content={"status": status, "postgres": postgres, "redis": redis})


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
