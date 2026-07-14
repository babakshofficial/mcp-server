from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, Request

from sync_mcp.auth import rbac
from sync_mcp.auth.service import resolve_bearer
from sync_mcp.config import Settings
from sync_mcp.models import AuthPrincipal, ProjectRole
from sync_mcp.storage.base import StateStore


def get_store(request: Request) -> StateStore:
    return request.app.state.store


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_optional_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    store: StateStore = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> AuthPrincipal | None:
    return await resolve_bearer(store, settings, authorization)


async def get_principal(
    principal: AuthPrincipal | None = Depends(get_optional_principal),
) -> AuthPrincipal:
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


async def require_hub_admin(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    if not rbac.can_manage_users(principal):
        raise HTTPException(status_code=403, detail="Hub admin required")
    return principal


def require_project_access(minimum: ProjectRole) -> Callable:
    async def _dep(
        project_id: str,
        principal: AuthPrincipal = Depends(get_principal),
        store: StateStore = Depends(get_store),
    ) -> AuthPrincipal:
        if principal.is_admin:
            project = await store.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
            return principal
        role = await store.get_project_role(project_id, principal.user_id)
        if not rbac.role_at_least(role, minimum):
            # Distinguish unknown project vs forbidden when possible.
            project = await store.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
            raise HTTPException(status_code=403, detail="Insufficient project permission")
        return principal

    return _dep
