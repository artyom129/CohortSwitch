from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.models import AuditEvent


def record_audit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID,
    request_id: str | None,
    actor_user_id: UUID | None = None,
    actor_api_key_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        metadata_json=metadata or {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    return event
