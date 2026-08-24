from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import APIKeyCreate, APIKeyCreated
from backend.app.core.errors import APIError
from backend.app.core.security import display_prefix, hash_secret, new_api_key
from backend.app.database.models import APIKey, Environment, Project
from backend.app.services.audit import record_audit


async def create_api_key(
    session: AsyncSession,
    *,
    payload: APIKeyCreate,
    kind: str,
    actor_user_id: UUID | None,
    actor_api_key_id: UUID | None,
    request_id: str | None,
) -> APIKeyCreated:
    if kind == "SDK":
        if not payload.project_id or not payload.environment_id:
            raise APIError(
                422, "invalid_sdk_key_scope", "SDK keys require project and environment."
            )
        project = await session.get(Project, payload.project_id)
        environment = await session.get(Environment, payload.environment_id)
        if (
            not project
            or project.organization_id != payload.organization_id
            or not environment
            or environment.project_id != project.id
        ):
            raise APIError(404, "scope_not_found", "Key scope was not found.")
        scopes = ["flags:read", "evaluate"]
        raw = new_api_key("cs_sdk_live_")
    elif kind == "MANAGEMENT":
        if payload.environment_id and not payload.project_id:
            raise APIError(
                422, "invalid_api_key_scope", "Environment scope requires project scope."
            )
        scopes = sorted(set(payload.scopes))
        if not scopes:
            raise APIError(422, "missing_scopes", "At least one management scope is required.")
        raw = new_api_key("cs_api_live_")
    else:
        raise ValueError(f"Unknown API key kind: {kind}")

    key = APIKey(
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        name=payload.name,
        kind=kind,
        key_prefix=display_prefix(raw),
        key_hash=hash_secret(raw),
        scopes=scopes,
        created_by_id=actor_user_id,
    )
    session.add(key)
    await session.flush()
    record_audit(
        session,
        organization_id=payload.organization_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=key.id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        metadata={"kind": kind, "prefix": key.key_prefix, "scopes": scopes},
    )
    return APIKeyCreated(
        id=key.id,
        name=key.name,
        kind=kind,
        key_prefix=key.key_prefix,
        key=raw,
        scopes=scopes,
        created_at=key.created_at or datetime.now(UTC),
    )
