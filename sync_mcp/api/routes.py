from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from sync_mcp.auth import rbac
from sync_mcp.auth.deps import get_principal, get_store, require_hub_admin, require_project_access
from sync_mcp.auth.passwords import hash_password
from sync_mcp.auth.service import authenticate_user, issue_access_token, mint_api_key, user_to_public
from sync_mcp.autosync import AutoSyncService
from sync_mcp.config import Settings
from sync_mcp.models import (
    ApiKeyCreate,
    ApiKeyCreated,
    AuthPrincipal,
    ChangeCreate,
    HubSettingsUpdate,
    LoginRequest,
    LoginResponse,
    ProjectCreate,
    ProjectMemberUpdate,
    ProjectRole,
    ProjectUpdate,
    PublishResult,
    SnapshotImport,
    SnapshotResult,
    Team,
    UserCreate,
    UserPublic,
    UserUpdate,
)
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.openapi_diff import endpoints_and_fingerprint
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import ProjectNotFoundError, UserNotFoundError


def create_api_router(
    store: StateStore,
    notifier: ChangeNotifier,
    settings: Settings,
    autosync: AutoSyncService | None = None,
) -> APIRouter:
    router = APIRouter()

    def _not_found(exc: ProjectNotFoundError) -> HTTPException:
        return HTTPException(status_code=404, detail=f"Project not found: {exc}")

    def _autosync(request: Request) -> AutoSyncService | None:
        return autosync or getattr(request.app.state, "autosync", None)

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- Auth ---

    @router.post("/auth/login")
    async def login(body: LoginRequest) -> LoginResponse:
        user = await authenticate_user(store, body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token = issue_access_token(settings, user)
        return LoginResponse(token=token, user=user_to_public(user))

    @router.get("/auth/me")
    async def me(principal: AuthPrincipal = Depends(get_principal)) -> UserPublic:
        user = await store.get_user(principal.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user_to_public(user)

    @router.post("/auth/logout")
    async def logout(principal: AuthPrincipal = Depends(get_principal)) -> dict[str, str]:
        return {"status": "ok", "detail": "Client should discard the token"}

    # --- Users (admin) ---

    @router.get("/users")
    async def list_users(_: AuthPrincipal = Depends(require_hub_admin)) -> list[UserPublic]:
        return await store.list_users()

    @router.post("/users")
    async def create_user(body: UserCreate, _: AuthPrincipal = Depends(require_hub_admin)) -> UserPublic:
        existing = await store.get_user_by_username(body.username)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Username already exists")
        user = await store.create_user(
            body.username,
            hash_password(body.password),
            hub_role=body.hub_role,
        )
        return user_to_public(user)

    @router.patch("/users/{user_id}")
    async def patch_user(
        user_id: str,
        body: UserUpdate,
        _: AuthPrincipal = Depends(require_hub_admin),
    ) -> UserPublic:
        try:
            user = await store.update_user(
                user_id,
                hub_role=body.hub_role,
                disabled=body.disabled,
                password_hash=hash_password(body.password) if body.password else None,
            )
        except UserNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"User not found: {exc}") from exc
        return user_to_public(user)

    # --- API keys ---

    @router.get("/api-keys")
    async def list_api_keys(principal: AuthPrincipal = Depends(get_principal)):
        if principal.is_admin:
            return await store.list_api_keys()
        return await store.list_api_keys(principal.user_id)

    @router.post("/api-keys")
    async def create_api_key_route(
        body: ApiKeyCreate,
        principal: AuthPrincipal = Depends(get_principal),
    ) -> ApiKeyCreated:
        target_user_id = body.user_id or principal.user_id
        if target_user_id != principal.user_id and not principal.is_admin:
            raise HTTPException(status_code=403, detail="Cannot mint API keys for other users")
        target = await store.get_user(target_user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        record, raw = await mint_api_key(store, target_user_id, body.name)
        return ApiKeyCreated(key=record, raw_key=raw)

    @router.delete("/api-keys/{key_id}")
    async def delete_api_key(key_id: str, principal: AuthPrincipal = Depends(get_principal)) -> dict[str, str]:
        keys = await store.list_api_keys()
        match = next((k for k in keys if k.id == key_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="API key not found")
        if match.user_id != principal.user_id and not principal.is_admin:
            raise HTTPException(status_code=403, detail="Cannot revoke another user's key")
        await store.revoke_api_key(key_id)
        return {"status": "revoked"}

    # --- Settings ---

    @router.get("/settings")
    async def get_settings(principal: AuthPrincipal = Depends(get_principal)):
        return await store.get_hub_settings()

    @router.put("/settings")
    async def put_settings(
        body: HubSettingsUpdate,
        request: Request,
        principal: AuthPrincipal = Depends(require_hub_admin),
    ):
        updated = await store.update_hub_settings(body)
        service = _autosync(request)
        if service is not None:
            service.notify_settings_changed()
        return updated

    # --- Projects ---

    @router.get("/projects")
    async def list_projects(principal: AuthPrincipal = Depends(get_principal)):
        return await store.list_projects_for_user(principal.user_id, is_admin=principal.is_admin)

    @router.post("/projects")
    async def create_project(
        body: ProjectCreate,
        request: Request,
        principal: AuthPrincipal = Depends(get_principal),
    ):
        if not rbac.can_create_project(principal):
            raise HTTPException(status_code=403, detail="Cannot create projects")
        project = await store.create_project(
            body.name,
            body.description,
            openapi_url=body.openapi_url,
            auto_sync=body.auto_sync,
            sync_mode=body.sync_mode.value,
            git_repo_path=body.git_repo_path,
            owner_user_id=principal.user_id,
        )
        service = _autosync(request)
        if service is not None and project.openapi_url and project.auto_sync:
            service.notify_settings_changed()
        return project

    @router.get("/projects/{project_id}")
    async def get_project(
        project_id: str,
        principal: AuthPrincipal = Depends(require_project_access(ProjectRole.viewer)),
    ):
        summaries = await store.list_projects_for_user(principal.user_id, is_admin=principal.is_admin)
        for summary in summaries:
            if summary.id == project_id:
                return summary
        project = await store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        return project

    @router.patch("/projects/{project_id}")
    async def patch_project(
        project_id: str,
        body: ProjectUpdate,
        request: Request,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.owner)),
    ):
        try:
            project = await store.update_project(project_id, body)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        service = _autosync(request)
        if service is not None:
            service.notify_settings_changed()
        return project

    @router.delete("/projects/{project_id}")
    async def delete_project(
        project_id: str,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.owner)),
    ) -> dict[str, str]:
        try:
            await store.delete_project(project_id)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        return {"status": "deleted", "project_id": project_id}

    @router.post("/projects/{project_id}/sync")
    async def sync_now(
        project_id: str,
        request: Request,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.editor)),
    ):
        project = await store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not project.openapi_url:
            raise HTTPException(status_code=400, detail="Project has no openapi_url configured")
        service = _autosync(request)
        if service is None:
            raise HTTPException(status_code=503, detail="Auto-sync service unavailable")
        changed = await service.sync_project(project, trigger="manual")
        refreshed = await store.get_project(project_id)
        return {"changed": changed, "project": refreshed}

    @router.post("/projects/{project_id}/hooks/commit")
    async def commit_hook(
        project_id: str,
        request: Request,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.editor)),
    ):
        project = await store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not project.openapi_url:
            raise HTTPException(status_code=400, detail="Project has no openapi_url configured")
        service = _autosync(request)
        if service is None:
            raise HTTPException(status_code=503, detail="Auto-sync service unavailable")
        body: dict = {}
        try:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}
        commit_sha = body.get("commit_sha") or body.get("sha") or body.get("after")
        if commit_sha is not None:
            commit_sha = str(commit_sha)
        changed = await service.sync_project(project, trigger="commit_hook", commit_sha=commit_sha)
        refreshed = await store.get_project(project_id)
        return {"changed": changed, "project": refreshed, "commit_sha": commit_sha}

    @router.get("/projects/{project_id}/members")
    async def list_members(
        project_id: str,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.viewer)),
    ):
        return await store.list_project_members(project_id)

    @router.put("/projects/{project_id}/members")
    async def upsert_member(
        project_id: str,
        body: ProjectMemberUpdate,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.owner)),
    ):
        user_id = body.user_id
        if not user_id and body.username:
            user = await store.get_user_by_username(body.username)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            user_id = user.id
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id or username required")
        try:
            return await store.set_project_member(project_id, user_id, body.role)
        except UserNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"User not found: {exc}") from exc
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc

    @router.delete("/projects/{project_id}/members/{user_id}")
    async def remove_member(
        project_id: str,
        user_id: str,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.owner)),
    ) -> dict[str, str]:
        await store.remove_project_member(project_id, user_id)
        return {"status": "removed"}

    @router.get("/projects/{project_id}/state")
    async def project_state(
        project_id: str,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.viewer)),
    ):
        try:
            return await store.get_state(project_id)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc

    @router.get("/projects/{project_id}/changelog")
    async def project_changelog(
        project_id: str,
        since: str | None = None,
        team: str | None = None,
        type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.viewer)),
    ):
        try:
            return await store.get_changelog(
                project_id,
                since=since,
                team=team,
                change_type=type,
                limit=limit,
            )
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc

    @router.post("/projects/{project_id}/updates")
    async def publish(
        project_id: str,
        change: ChangeCreate,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.editor)),
    ) -> PublishResult:
        try:
            saved, next_state = await store.publish(project_id, change)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        await notifier.publish(saved, next_state)
        return PublishResult(
            change_id=saved.id,
            project_id=project_id,
            version=saved.version,
            state=next_state,
        )

    @router.post("/projects/{project_id}/snapshot")
    async def import_snapshot(
        project_id: str,
        snapshot: SnapshotImport,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.editor)),
    ) -> SnapshotResult:
        try:
            saved, next_state = await store.import_snapshot(project_id, snapshot)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        await notifier.publish(saved, next_state)
        return SnapshotResult(
            project_id=project_id,
            team=snapshot.team,
            change_id=saved.id,
            version=saved.version,
            state=next_state,
        )

    @router.post("/projects/{project_id}/openapi")
    async def import_openapi(
        project_id: str,
        body: dict,
        request: Request,
        _: AuthPrincipal = Depends(require_project_access(ProjectRole.editor)),
    ):
        openapi = body.get("openapi") or body.get("spec")
        openapi_url = str(body.get("openapi_url") or "")
        if not isinstance(openapi, dict):
            raise HTTPException(status_code=400, detail="Body must include openapi object")
        team = Team(body.get("team") or "backend")
        endpoints, fingerprint = endpoints_and_fingerprint(openapi, team=team)
        notes = str(body.get("notes") or f"Imported from OpenAPI ({len(endpoints)} endpoints)")
        snapshot = SnapshotImport(team=team, api=endpoints, notes=notes)
        try:
            saved, next_state = await store.import_snapshot(project_id, snapshot)
            if openapi_url:
                await store.update_project(
                    project_id,
                    ProjectUpdate(openapi_url=openapi_url, auto_sync=True),
                )
                await store.update_sync_status(
                    project_id,
                    status="updated",
                    error="",
                    fingerprint=fingerprint,
                )
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        await notifier.publish(saved, next_state)
        service = _autosync(request)
        if service is not None:
            service.notify_settings_changed()
        return SnapshotResult(
            project_id=project_id,
            team=snapshot.team,
            change_id=saved.id,
            version=saved.version,
            state=next_state,
        )

    @router.get("/state")
    async def state(
        project_id: str | None = None,
        principal: AuthPrincipal = Depends(get_principal),
        store_dep: StateStore = Depends(get_store),
    ):
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
        if not principal.is_admin:
            role = await store_dep.get_project_role(project_id, principal.user_id)
            if not rbac.role_at_least(role, ProjectRole.viewer):
                raise HTTPException(status_code=403, detail="Insufficient project permission")
        try:
            return await store.get_state(project_id)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc

    @router.get("/changelog")
    async def changelog(
        project_id: str | None = None,
        since: str | None = None,
        team: str | None = None,
        type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        principal: AuthPrincipal = Depends(get_principal),
        store_dep: StateStore = Depends(get_store),
    ):
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
        if not principal.is_admin:
            role = await store_dep.get_project_role(project_id, principal.user_id)
            if not rbac.role_at_least(role, ProjectRole.viewer):
                raise HTTPException(status_code=403, detail="Insufficient project permission")
        try:
            return await store.get_changelog(
                project_id,
                since=since,
                team=team,
                change_type=type,
                limit=limit,
            )
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc

    @router.post("/updates")
    async def publish_legacy(
        change: ChangeCreate,
        project_id: str | None = None,
        principal: AuthPrincipal = Depends(get_principal),
        store_dep: StateStore = Depends(get_store),
    ) -> PublishResult:
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
        if not principal.is_admin:
            role = await store_dep.get_project_role(project_id, principal.user_id)
            if not rbac.role_at_least(role, ProjectRole.editor):
                raise HTTPException(status_code=403, detail="Insufficient project permission")
        try:
            saved, next_state = await store.publish(project_id, change)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        await notifier.publish(saved, next_state)
        return PublishResult(
            change_id=saved.id,
            project_id=project_id,
            version=saved.version,
            state=next_state,
        )

    @router.get("/events")
    async def events(
        request: Request,
        access_token: str | None = None,
        store_dep: StateStore = Depends(get_store),
    ):
        from sync_mcp.auth.service import resolve_bearer

        authorization = request.headers.get("authorization")
        if not authorization and access_token:
            authorization = f"Bearer {access_token}"
        principal = await resolve_bearer(store_dep, settings, authorization)
        if principal is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return StreamingResponse(notifier.stream(), media_type="text/event-stream")

    return router
