from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import (
    Principal,
    authorize_environment,
    authorize_organization,
    authorize_project,
    get_principal,
)
from backend.app.api.schemas import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyResponse,
    AuditResponse,
    ConfigBundle,
    EnvironmentCreate,
    EnvironmentResponse,
    FlagCreate,
    FlagResponse,
    FlagUpdate,
    OrganizationCreate,
    OrganizationResponse,
    ProjectCreate,
    ProjectResponse,
    RuleCreate,
    ScheduledChangeCreate,
    VersionResponse,
)
from backend.app.core.errors import APIError
from backend.app.core.permissions import Permission
from backend.app.core.redis import redis_client
from backend.app.database.models import (
    APIKey,
    AuditEvent,
    Environment,
    FeatureFlag,
    FlagConfig,
    FlagVersion,
    Membership,
    Organization,
    Project,
    ScheduledChange,
)
from backend.app.database.session import get_session
from backend.app.services.audit import record_audit
from backend.app.services.cache import invalidate_and_publish
from backend.app.services.flags import (
    _response,
    append_rule_update,
    compile_configuration,
    create_flag,
    get_all_environment_configs,
    get_config_by_flag_environment,
    rollback_flag,
    update_flag,
)
from backend.app.services.keys import create_api_key

router = APIRouter(prefix="/api/v1", tags=["management"])


class RollbackRequest(BaseModel):
    environment_id: UUID
    expected_version: int = Field(ge=1)


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    payload: OrganizationCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Organization:
    if not principal.user_id:
        raise APIError(403, "user_auth_required", "A user access token is required.")
    organization = Organization(name=payload.name, slug=payload.slug)
    session.add(organization)
    await session.flush()
    session.add(
        Membership(organization_id=organization.id, user_id=principal.user_id, role="OWNER")
    )
    record_audit(
        session,
        organization_id=organization.id,
        action="organization.created",
        resource_type="organization",
        resource_id=organization.id,
        request_id=request.state.request_id,
        actor_user_id=principal.user_id,
        metadata={"slug": organization.slug},
    )
    await session.commit()
    return organization


@router.get("/organizations", response_model=list[OrganizationResponse])
async def list_organizations(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[Organization]:
    if not principal.user_id:
        raise APIError(403, "user_auth_required", "A user access token is required.")
    result = await session.scalars(
        select(Organization)
        .join(Membership)
        .where(Membership.user_id == principal.user_id)
        .order_by(Organization.name)
    )
    return list(result.all())


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Project:
    await authorize_organization(
        session, principal, payload.organization_id, Permission.PROJECT_MANAGE
    )
    project = Project(organization_id=payload.organization_id, name=payload.name, slug=payload.slug)
    session.add(project)
    await session.flush()
    record_audit(
        session,
        organization_id=payload.organization_id,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        request_id=request.state.request_id,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        metadata={"slug": project.slug},
    )
    await session.commit()
    return project


@router.post(
    "/projects/{project_id}/environments", response_model=EnvironmentResponse, status_code=201
)
async def create_environment(
    project_id: UUID,
    payload: EnvironmentCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Environment:
    project, _ = await authorize_project(
        session, principal, project_id, Permission.ENVIRONMENT_MANAGE
    )
    environment = Environment(project_id=project.id, key=payload.key, name=payload.name)
    session.add(environment)
    await session.flush()
    record_audit(
        session,
        organization_id=project.organization_id,
        action="environment.created",
        resource_type="environment",
        resource_id=environment.id,
        request_id=request.state.request_id,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        metadata={"key": environment.key},
    )
    await session.commit()
    return environment


@router.post("/projects/{project_id}/flags", response_model=FlagResponse, status_code=201)
async def create_feature_flag(
    project_id: UUID,
    payload: FlagCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FlagResponse:
    project, _ = await authorize_project(session, principal, project_id, Permission.FLAG_WRITE)
    environment, _, _ = await authorize_environment(
        session, principal, payload.environment_id, Permission.FLAG_WRITE
    )
    result = await create_flag(
        session,
        project=project,
        environment=environment,
        payload=payload,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        request_id=request.state.request_id,
    )
    await session.commit()
    await invalidate_and_publish(
        redis_client,
        project.id,
        environment.id,
        result.key,
        "flag.created",
        result.current_version,
        environment.revision,
    )
    return result


@router.get("/projects/{project_id}/flags", response_model=list[FlagResponse])
async def list_flags(
    project_id: UUID,
    environment_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[FlagResponse]:
    await authorize_project(session, principal, project_id, Permission.FLAG_READ)
    await authorize_environment(session, principal, environment_id, Permission.FLAG_READ)
    configs = await get_all_environment_configs(session, project_id, environment_id)
    flags = await session.scalars(
        select(FeatureFlag).where(FeatureFlag.id.in_([UUID(item.flag_id) for item in configs]))
    )
    by_id = {str(flag.id): flag for flag in flags.all()}
    return [_response(by_id[item.flag_id], item) for item in configs]


@router.get("/flags/{flag_id}", response_model=FlagResponse)
async def get_flag(
    flag_id: UUID,
    environment_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FlagResponse:
    config = await get_config_by_flag_environment(session, flag_id, environment_id)
    await authorize_project(session, principal, config.flag.project_id, Permission.FLAG_READ)
    return _response(config.flag, compile_configuration(config))


@router.put("/flags/{flag_id}", response_model=FlagResponse)
async def replace_flag_configuration(
    flag_id: UUID,
    payload: FlagUpdate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FlagResponse:
    flag = await session.get(FeatureFlag, flag_id)
    if not flag:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    project, _ = await authorize_project(session, principal, flag.project_id, Permission.FLAG_WRITE)
    environment, _, _ = await authorize_environment(
        session, principal, payload.environment_id, Permission.FLAG_WRITE
    )
    previous_revision = environment.revision
    result = await update_flag(
        session,
        flag=flag,
        environment=environment,
        project=project,
        payload=payload,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        request_id=request.state.request_id,
    )
    environment.revision = previous_revision + 1
    await session.commit()
    await invalidate_and_publish(
        redis_client,
        project.id,
        environment.id,
        flag.key,
        "flag.updated",
        result.current_version,
        environment.revision,
    )
    return result


@router.post("/flags/{flag_id}/rules", response_model=FlagResponse, status_code=201)
async def add_targeting_rule(
    flag_id: UUID,
    payload: RuleCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FlagResponse:
    config = await get_config_by_flag_environment(session, flag_id, payload.environment_id)
    project, _ = await authorize_project(
        session, principal, config.flag.project_id, Permission.FLAG_WRITE
    )
    environment = config.environment
    previous_revision = environment.revision
    update_payload = await append_rule_update(
        session, config, payload.rule, payload.expected_version
    )
    result = await update_flag(
        session,
        flag=config.flag,
        environment=environment,
        project=project,
        payload=update_payload,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        request_id=request.state.request_id,
        action="rollout.changed" if payload.rule.rollout else "flag.updated",
    )
    environment.revision = previous_revision + 1
    await session.commit()
    await invalidate_and_publish(
        redis_client,
        project.id,
        environment.id,
        config.flag.key,
        "rollout.changed" if payload.rule.rollout else "flag.updated",
        result.current_version,
        environment.revision,
    )
    return result


@router.post("/flags/{flag_id}/rollback/{version}", response_model=FlagResponse)
async def rollback_feature_flag(
    flag_id: UUID,
    version: int,
    payload: RollbackRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FlagResponse:
    flag = await session.get(FeatureFlag, flag_id)
    if not flag:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    project, _ = await authorize_project(session, principal, flag.project_id, Permission.FLAG_WRITE)
    environment, _, _ = await authorize_environment(
        session, principal, payload.environment_id, Permission.FLAG_WRITE
    )
    previous_revision = environment.revision
    result = await rollback_flag(
        session,
        flag=flag,
        environment=environment,
        project=project,
        target_version=version,
        expected_version=payload.expected_version,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        request_id=request.state.request_id,
    )
    environment.revision = previous_revision + 1
    await session.commit()
    await invalidate_and_publish(
        redis_client,
        project.id,
        environment.id,
        flag.key,
        "flag.rollback",
        result.current_version,
        environment.revision,
    )
    return result


@router.get("/flags/{flag_id}/versions", response_model=list[VersionResponse])
async def flag_versions(
    flag_id: UUID,
    environment_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[FlagVersion]:
    config = await get_config_by_flag_environment(session, flag_id, environment_id)
    await authorize_project(session, principal, config.flag.project_id, Permission.FLAG_READ)
    result = await session.scalars(
        select(FlagVersion)
        .where(FlagVersion.config_id == config.id)
        .order_by(FlagVersion.version.desc())
    )
    return list(result.all())


@router.delete("/flags/{flag_id}", status_code=204)
async def archive_flag(
    flag_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    flag = await session.get(FeatureFlag, flag_id)
    if not flag or flag.archived_at is not None:
        raise APIError(404, "flag_not_found", "Feature flag was not found.")
    project, _ = await authorize_project(session, principal, flag.project_id, Permission.FLAG_WRITE)
    configs = await session.scalars(select(FlagConfig).where(FlagConfig.flag_id == flag.id))
    config_rows = list(configs.all())
    flag.archived_at = datetime.now(UTC)
    for config in config_rows:
        environment = await session.get(Environment, config.environment_id)
        if environment:
            environment.revision += 1
    record_audit(
        session,
        organization_id=project.organization_id,
        action="flag.deleted",
        resource_type="feature_flag",
        resource_id=flag.id,
        request_id=request.state.request_id,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        metadata={"flag_key": flag.key, "archived": True},
    )
    await session.commit()
    for config in config_rows:
        environment = await session.get(Environment, config.environment_id)
        if environment:
            await invalidate_and_publish(
                redis_client,
                project.id,
                environment.id,
                flag.key,
                "flag.deleted",
                config.current_version,
                environment.revision,
            )


@router.post("/flags/{flag_id}/scheduled-changes", status_code=201)
async def schedule_change(
    flag_id: UUID,
    payload: ScheduledChangeCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    config = await get_config_by_flag_environment(session, flag_id, payload.environment_id)
    await authorize_project(session, principal, config.flag.project_id, Permission.FLAG_WRITE)
    if config.current_version != payload.expected_version:
        raise APIError(
            409,
            "configuration_conflict",
            "The flag configuration was changed by another writer.",
            details={"current_version": config.current_version},
        )
    scheduled = ScheduledChange(
        config_id=config.id,
        change_type=payload.change_type,
        payload=payload.payload,
        activate_at=payload.activate_at,
        created_by_id=principal.user_id,
    )
    session.add(scheduled)
    await session.flush()
    record_audit(
        session,
        organization_id=config.flag.project.organization_id,
        action="flag.change_scheduled",
        resource_type="scheduled_change",
        resource_id=scheduled.id,
        request_id=request.state.request_id,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        metadata={
            "change_type": payload.change_type,
            "activate_at": payload.activate_at.isoformat(),
        },
    )
    await session.commit()
    return {"id": str(scheduled.id), "status": "scheduled"}


async def _create_key_endpoint(
    payload: APIKeyCreate,
    kind: str,
    request: Request,
    principal: Principal,
    session: AsyncSession,
) -> APIKeyCreated:
    await authorize_organization(session, principal, payload.organization_id, Permission.KEY_MANAGE)
    result = await create_api_key(
        session,
        payload=payload,
        kind=kind,
        actor_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        request_id=request.state.request_id,
    )
    await session.commit()
    return result


@router.post("/sdk-keys", response_model=APIKeyCreated, status_code=201)
async def create_sdk_key(
    payload: APIKeyCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> APIKeyCreated:
    return await _create_key_endpoint(payload, "SDK", request, principal, session)


@router.post("/api-keys", response_model=APIKeyCreated, status_code=201)
async def create_management_key(
    payload: APIKeyCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> APIKeyCreated:
    return await _create_key_endpoint(payload, "MANAGEMENT", request, principal, session)


@router.get("/organizations/{organization_id}/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    organization_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[APIKey]:
    await authorize_organization(session, principal, organization_id, Permission.KEY_MANAGE)
    result = await session.scalars(
        select(APIKey)
        .where(APIKey.organization_id == organization_id)
        .order_by(APIKey.created_at.desc())
    )
    return list(result.all())


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    key = await session.get(APIKey, key_id)
    if not key:
        raise APIError(404, "api_key_not_found", "API key was not found.")
    await authorize_organization(session, principal, key.organization_id, Permission.KEY_MANAGE)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        record_audit(
            session,
            organization_id=key.organization_id,
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=key.id,
            request_id=request.state.request_id,
            actor_user_id=principal.user_id,
            actor_api_key_id=principal.api_key_id,
            metadata={"prefix": key.key_prefix},
        )
    await session.commit()


@router.get("/organizations/{organization_id}/audit", response_model=list[AuditResponse])
async def list_audit_events(
    organization_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    before: datetime | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEvent]:
    await authorize_organization(session, principal, organization_id, Permission.AUDIT_READ)
    statement = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
    if before:
        statement = statement.where(AuditEvent.created_at < before)
    result = await session.scalars(statement.order_by(AuditEvent.created_at.desc()).limit(limit))
    return list(result.all())


@router.get(
    "/projects/{project_id}/environments/{environment_id}/config",
    response_model=ConfigBundle,
)
async def export_configuration(
    project_id: UUID,
    environment_id: UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ConfigBundle | Response:
    if principal.kind == "SDK":
        if (
            principal.project_id != project_id
            or principal.environment_id != environment_id
            or "flags:read" not in principal.scopes
        ):
            raise APIError(403, "insufficient_scope", "SDK key is not scoped to this environment.")
        environment = await session.get(Environment, environment_id)
    else:
        environment, _, _ = await authorize_environment(
            session, principal, environment_id, Permission.FLAG_READ
        )
    if not environment or environment.project_id != project_id:
        raise APIError(404, "environment_not_found", "Environment was not found.")
    etag = f'"{environment.revision}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    configs = await get_all_environment_configs(session, project_id, environment_id)
    response.headers["ETag"] = etag
    return ConfigBundle(
        project_id=project_id,
        environment_id=environment_id,
        revision=environment.revision,
        flags=[item.model_dump(mode="json") for item in configs],
    )
