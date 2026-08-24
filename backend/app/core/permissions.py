from enum import StrEnum

from backend.app.core.errors import APIError


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    DEVELOPER = "DEVELOPER"
    VIEWER = "VIEWER"


class Permission(StrEnum):
    ORG_MANAGE = "org:manage"
    PROJECT_MANAGE = "project:manage"
    ENVIRONMENT_MANAGE = "environment:manage"
    FLAG_READ = "flags:read"
    FLAG_WRITE = "flags:write"
    EVALUATE = "evaluate"
    AUDIT_READ = "audit:read"
    KEY_MANAGE = "keys:manage"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(
        {
            Permission.ORG_MANAGE,
            Permission.PROJECT_MANAGE,
            Permission.ENVIRONMENT_MANAGE,
            Permission.FLAG_READ,
            Permission.FLAG_WRITE,
            Permission.EVALUATE,
            Permission.AUDIT_READ,
            Permission.KEY_MANAGE,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Permission.FLAG_READ,
            Permission.FLAG_WRITE,
            Permission.EVALUATE,
            Permission.AUDIT_READ,
        }
    ),
    Role.VIEWER: frozenset({Permission.FLAG_READ, Permission.EVALUATE}),
}


def require_role_permission(role: str, permission: Permission) -> None:
    try:
        allowed = ROLE_PERMISSIONS[Role(role)]
    except ValueError as exc:
        raise APIError(403, "permission_denied", "The requested operation is not allowed.") from exc
    if permission not in allowed:
        raise APIError(403, "permission_denied", "The requested operation is not allowed.")
