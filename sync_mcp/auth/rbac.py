from __future__ import annotations

from sync_mcp.models import AuthPrincipal, HubRole, PROJECT_ROLE_RANK, ProjectRole


def role_at_least(actual: ProjectRole | None, minimum: ProjectRole) -> bool:
    if actual is None:
        return False
    return PROJECT_ROLE_RANK[actual] >= PROJECT_ROLE_RANK[minimum]


def can_create_project(principal: AuthPrincipal) -> bool:
    return principal.hub_role in {HubRole.admin, HubRole.member} and True


def can_manage_users(principal: AuthPrincipal) -> bool:
    return principal.is_admin


def can_manage_hub_settings(principal: AuthPrincipal) -> bool:
    return principal.is_admin
