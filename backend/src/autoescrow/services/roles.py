from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoescrow.core.errors import ApiError
from autoescrow.core.permissions import Permission, has_permission
from autoescrow.models.enums import RoleCode
from autoescrow.models.identity import User
from autoescrow.models.rbac import Role, UserRole
from autoescrow.services.audit import AuditEventType, record_audit


def _require_role_manager(actor_roles: set[str]) -> None:
    if not has_permission(actor_roles, Permission.ROLE_ASSIGN):
        raise ApiError(403, "forbidden", "You do not have permission to manage roles.")


async def assign_role(
    session: AsyncSession,
    *,
    actor_user_id: UUID,
    actor_roles: set[str],
    target_user_id: UUID,
    role_code: RoleCode,
) -> bool:
    """Controlled role assignment foundation; intentionally not exposed as an endpoint."""

    _require_role_manager(actor_roles)

    target = await session.get(User, target_user_id)
    if target is None:
        raise ApiError(404, "user_not_found", "User not found.")
    role = await session.scalar(select(Role).where(Role.code == role_code.value))
    if role is None:
        raise ApiError(404, "role_not_found", "Role not found.")

    existing = await session.get(UserRole, (target_user_id, role.id))
    if existing is not None:
        return False

    session.add(
        UserRole(
            user_id=target_user_id,
            role_id=role.id,
            assigned_by_user_id=actor_user_id,
        )
    )
    await record_audit(
        session,
        event_type=AuditEventType.ROLE_ASSIGNED,
        subject_type="user",
        subject_id=target_user_id,
        actor_user_id=actor_user_id,
        metadata={"role_code": role.code},
    )
    await session.commit()
    return True


async def revoke_role(
    session: AsyncSession,
    *,
    actor_user_id: UUID,
    actor_roles: set[str],
    target_user_id: UUID,
    role_code: RoleCode,
) -> bool:
    _require_role_manager(actor_roles)

    role = await session.scalar(select(Role).where(Role.code == role_code.value))
    if role is None:
        raise ApiError(404, "role_not_found", "Role not found.")
    assignment = await session.get(UserRole, (target_user_id, role.id))
    if assignment is None:
        return False

    if role.code == RoleCode.ADMIN.value:
        admin_count = await session.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
        )
        if (admin_count or 0) <= 1:
            raise ApiError(409, "last_admin", "The last administrator role cannot be removed.")

    await session.execute(
        delete(UserRole).where(
            UserRole.user_id == target_user_id,
            UserRole.role_id == role.id,
        )
    )
    await record_audit(
        session,
        event_type=AuditEventType.ROLE_REVOKED,
        subject_type="user",
        subject_id=target_user_id,
        actor_user_id=actor_user_id,
        metadata={"role_code": role.code},
    )
    await session.commit()
    return True
