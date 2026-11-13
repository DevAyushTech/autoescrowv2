from collections.abc import Iterable
from enum import StrEnum

from autoescrow.models.enums import RoleCode


class Permission(StrEnum):
    PROFILE_READ = "profile:read"
    PROFILE_UPDATE = "profile:update"
    WALLET_READ = "wallet:read"
    WALLET_WRITE = "wallet:write"
    SESSION_READ = "session:read"
    SESSION_REVOKE = "session:revoke"
    AUDIT_READ = "audit:read"
    ROLE_ASSIGN = "role:assign"


_BASE_PERMISSIONS = frozenset(
    {
        Permission.PROFILE_READ,
        Permission.PROFILE_UPDATE,
        Permission.WALLET_READ,
        Permission.WALLET_WRITE,
        Permission.SESSION_READ,
        Permission.SESSION_REVOKE,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    RoleCode.USER.value: _BASE_PERMISSIONS,
    RoleCode.AGENT_OWNER.value: _BASE_PERMISSIONS,
    RoleCode.ARBITRATOR.value: _BASE_PERMISSIONS | {Permission.AUDIT_READ},
    RoleCode.ADMIN.value: frozenset(Permission),
}


def permissions_for_roles(roles: Iterable[str]) -> frozenset[Permission]:
    """Resolve permissions from role codes. Unknown roles grant nothing."""

    permissions: set[Permission] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, frozenset()))
    return frozenset(permissions)


def has_permission(roles: Iterable[str], permission: Permission) -> bool:
    return permission in permissions_for_roles(roles)
