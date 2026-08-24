from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.core.errors import APIError
from backend.app.core.metrics import CACHE_OPERATIONS
from backend.app.evaluation.models import FlagConfiguration
from backend.app.services.flags import get_config_by_key, get_configs_by_keys

logger = structlog.get_logger()


def config_cache_key(project_id: UUID, environment_id: UUID, flag_key: str) -> str:
    return f"cohortswitch:config:{project_id}:{environment_id}:{flag_key}"


async def load_configuration(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    project_id: UUID,
    environment_id: UUID,
    flag_key: str,
) -> FlagConfiguration:
    key = config_cache_key(project_id, environment_id, flag_key)
    try:
        payload = await redis.get(key)
        if payload:
            CACHE_OPERATIONS.labels("get", "hit").inc()
            return FlagConfiguration.model_validate_json(payload)
        CACHE_OPERATIONS.labels("get", "miss").inc()
    except RedisError as exc:
        CACHE_OPERATIONS.labels("get", "unavailable").inc()
        await logger.warning("config_cache_read_failed", error=type(exc).__name__)

    config = await get_config_by_key(session, project_id, environment_id, flag_key)
    try:
        await redis.set(key, config.model_dump_json(), ex=settings.cache_ttl_seconds)
        CACHE_OPERATIONS.labels("set", "ok").inc()
    except RedisError as exc:
        CACHE_OPERATIONS.labels("set", "unavailable").inc()
        await logger.warning("config_cache_write_failed", error=type(exc).__name__)
    return config


async def load_configurations(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    project_id: UUID,
    environment_id: UUID,
    flag_keys: list[str],
) -> dict[str, FlagConfiguration]:
    unique_keys = list(dict.fromkeys(flag_keys))
    cache_keys = [config_cache_key(project_id, environment_id, key) for key in unique_keys]
    cached: list[bytes | None] = [None] * len(cache_keys)
    try:
        cached = await redis.mget(cache_keys)
    except RedisError as exc:
        await logger.warning("config_cache_batch_read_failed", error=type(exc).__name__)
    configs: dict[str, FlagConfiguration] = {}
    misses: list[str] = []
    for flag_key, payload in zip(unique_keys, cached, strict=True):
        if payload:
            configs[flag_key] = FlagConfiguration.model_validate_json(payload)
        else:
            misses.append(flag_key)
    if misses:
        loaded = await get_configs_by_keys(session, project_id, environment_id, misses)
        configs.update(loaded)
        try:
            pipeline = redis.pipeline(transaction=False)
            for flag_key, config in loaded.items():
                pipeline.set(
                    config_cache_key(project_id, environment_id, flag_key),
                    config.model_dump_json(),
                    ex=settings.cache_ttl_seconds,
                )
            if loaded:
                await pipeline.execute()
        except RedisError as exc:
            await logger.warning("config_cache_batch_write_failed", error=type(exc).__name__)
    missing = sorted(set(unique_keys) - configs.keys())
    if missing:
        raise APIError(
            404, "flags_not_found", "One or more flags were not found.", details={"flags": missing}
        )
    return configs


async def invalidate_and_publish(
    redis: Redis,
    project_id: UUID,
    environment_id: UUID,
    flag_key: str,
    event: str,
    version: int,
    revision: int,
) -> None:
    try:
        await redis.delete(config_cache_key(project_id, environment_id, flag_key))
        await redis.publish(
            f"cohortswitch:events:{project_id}:{environment_id}",
            FlagConfiguration.model_config.get("json_dumps", None)
            or __import__("json").dumps(
                {
                    "event": event,
                    "flag_key": flag_key,
                    "config_version": version,
                    "revision": revision,
                }
            ),
        )
    except RedisError as exc:
        await logger.warning("config_invalidation_failed", error=type(exc).__name__)
