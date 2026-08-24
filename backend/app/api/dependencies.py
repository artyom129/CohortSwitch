from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import APIError
from backend.app.core.permissions import Permission, require_role_permission
from backend.app.core.security import decode_access_token, hash_secret
from backend.app.database.models import APIKey, Environment, Membership, Project, User
from backend.app.database.session import get_session

bearer = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class Principal:
    user_id: UUID | None = None
    api_key_id: UUID | None = None
    kind: str = "USER"
    organization_id: UUID | None = None
    project_id: UUID | None = None
    environment_id: UUID | None = None
    scopes: frozenset[str] = frozenset()


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    api_key_header: str | None = Header(default=None, alias="X-CohortSwitch-Key"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    raw = api_key_header
    if raw is None and credentials and credentials.credentials.startswith("cs_"):
        raw = credentials.credentials
    if raw:
        key = await session.scalar(
            select(APIKey).where(APIKey.key_hash == hash_secret(raw), APIKey.revoked_at.is_(None))
        )
        if not key:
            raise APIError(401, "invalid_api_key", "API key is invalid or revoked.")
        key.last_used_at = datetime.now(UTC)
        request.state.api_key_id = key.id
        return Principal(
            api_key_id=key.id,
            kind=key.kind,
            organization_id=key.organization_id,
            project_id=key.project_id,
            environment_id=key.environment_id,
            scopes=frozenset(key.scopes),
        )

    if not credentials:
        raise APIError(401, "authentication_required", "Authentication is required.")
    try:
        claims = decode_access_token(credentials.credentials, settings)
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise APIError(401, "invalid_access_token", "Access token is invalid or expired.") from exc
    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise APIError(401, "account_inactive", "The account is inactive.")
    request.state.user_id = user.id
    return Principal(user_id=user.id)


async def get_current_user(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not principal.user_id:
        raise APIError(403, "user_auth_required", "A user access token is required.")
    user = await session.get(User, principal.user_id)
    if not user:
        raise APIError(401, "account_inactive", "The account is inactive.")
    return user


async def membership_for(
    session: AsyncSession, principal: Principal, organization_id: UUID
) -> Membership:
    if principal.kind == "MANAGEMENT":
        if principal.organization_id != organization_id:
            raise APIError(404, "organization_not_found", "Organization was not found.")
        return Membership(organization_id=organization_id, user_id=UUID(int=0), role="OWNER")
    if not principal.user_id:
        raise APIError(403, "management_auth_required", "Management credentials are required.")
    membership = await session.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == principal.user_id,
        )
    )
    if not membership:
        raise APIError(404, "organization_not_found", "Organization was not found.")
    return membership


async def authorize_organization(
    session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    permission: Permission,
) -> Membership:
    membership = await membership_for(session, principal, organization_id)
    if principal.kind == "MANAGEMENT":
        if permission.value not in principal.scopes:
            raise APIError(403, "insufficient_scope", "API key lacks the required scope.")
    else:
        require_role_permission(membership.role, permission)
    return membership


async def authorize_project(
    session: AsyncSession,
    principal: Principal,
    project_id: UUID,
    permission: Permission,
) -> tuple[Project, Membership]:
    project = await session.get(Project, project_id)
    if not project:
        raise APIError(404, "project_not_found", "Project was not found.")
    if principal.project_id and principal.project_id != project_id:
        raise APIError(404, "project_not_found", "Project was not found.")
    membership = await authorize_organization(
        session, principal, project.organization_id, permission
    )
    return project, membership


async def authorize_environment(
    session: AsyncSession,
    principal: Principal,
    environment_id: UUID,
    permission: Permission,
) -> tuple[Environment, Project, Membership]:
    environment = await session.get(Environment, environment_id)
    if not environment:
        raise APIError(404, "environment_not_found", "Environment was not found.")
    if principal.environment_id and principal.environment_id != environment_id:
        raise APIError(404, "environment_not_found", "Environment was not found.")
    project, membership = await authorize_project(
        session, principal, environment.project_id, permission
    )
    return environment, project, membership


def require_api_scope(principal: Principal, scope: str) -> None:
    if principal.kind not in {"SDK", "MANAGEMENT"} or scope not in principal.scopes:
        raise APIError(403, "insufficient_scope", "API key lacks the required scope.")
