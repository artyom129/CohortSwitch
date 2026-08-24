import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from backend.app.api.schemas import RolloutInput
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.core.redis import redis_client
from backend.app.database.models import FlagConfig, Project, ScheduledChange
from backend.app.database.session import SessionFactory
from backend.app.services.cache import invalidate_and_publish
from backend.app.services.flags import (
    _update_from_configuration,
    compile_configuration,
    get_config_by_flag_environment,
    update_flag,
)

logger = structlog.get_logger()


def _apply_payload(change: ScheduledChange, update_payload: Any) -> tuple[str, str]:
    if change.change_type == "enable":
        update_payload.enabled = True
        return "flag.enabled", "flag.updated"
    if change.change_type == "disable":
        update_payload.enabled = False
        return "flag.disabled", "flag.updated"
    if change.change_type == "variation":
        update_payload.default_variation = str(change.payload["variation"])
        update_payload.rollout = []
        update_payload.progressive_stages = []
        return "flag.updated", "flag.updated"
    if change.change_type == "rollout_percentage":
        percentage = int(change.payload["percentage_basis_points"])
        if not 0 <= percentage <= 10_000:
            raise ValueError("percentage_basis_points must be between 0 and 10000")
        variation = str(change.payload["variation"])
        fallback = str(change.payload["fallback_variation"])
        allocations = (
            [RolloutInput(variation=variation, percentage_basis_points=percentage)]
            if percentage
            else []
        )
        if percentage < 10_000:
            allocations.append(
                RolloutInput(
                    variation=fallback,
                    percentage_basis_points=10_000 - percentage,
                )
            )
        update_payload.rollout = allocations
        update_payload.progressive_stages = []
        return "rollout.changed", "rollout.changed"
    raise ValueError(f"Unsupported scheduled change type: {change.change_type}")


async def apply_due_changes() -> int:
    notifications: list[tuple[Any, Any, str, str, int, int]] = []
    async with SessionFactory() as session, session.begin():
        rows = await session.scalars(
            select(ScheduledChange)
            .where(
                ScheduledChange.applied_at.is_(None),
                ScheduledChange.activate_at <= datetime.now(UTC),
            )
            .order_by(ScheduledChange.activate_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
        for change in rows.all():
            config_row = await session.get(FlagConfig, change.config_id)
            if not config_row:
                change.applied_at = datetime.now(UTC)
                continue
            config = await get_config_by_flag_environment(
                session, config_row.flag_id, config_row.environment_id
            )
            project = await session.get(Project, config.flag.project_id)
            if not project:
                change.applied_at = datetime.now(UTC)
                continue
            compiled = compile_configuration(config)
            update_payload = _update_from_configuration(
                compiled, config.environment_id, config.current_version
            )
            audit_action, event = _apply_payload(change, update_payload)
            previous_revision = config.environment.revision
            result = await update_flag(
                session,
                flag=config.flag,
                environment=config.environment,
                project=project,
                payload=update_payload,
                actor_user_id=change.created_by_id,
                actor_api_key_id=None,
                request_id=None,
                action=audit_action,
            )
            config.environment.revision = previous_revision + 1
            change.applied_at = datetime.now(UTC)
            notifications.append(
                (
                    project.id,
                    config.environment_id,
                    config.flag.key,
                    event,
                    result.current_version,
                    config.environment.revision,
                )
            )
    for project_id, environment_id, flag_key, event, version, revision in notifications:
        await invalidate_and_publish(
            redis_client,
            project_id,
            environment_id,
            flag_key,
            event,
            version,
            revision,
        )
    return len(notifications)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await logger.info("scheduler_started")
    try:
        while True:
            try:
                applied = await apply_due_changes()
                if applied:
                    await logger.info("scheduled_changes_applied", count=applied)
            except Exception:
                await logger.exception("scheduler_iteration_failed")
            await asyncio.sleep(2)
    finally:
        await redis_client.aclose()


__all__ = ["apply_due_changes", "run"]
