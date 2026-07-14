from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from sync_mcp.autosync import AutoSyncService
from sync_mcp.config import Settings
from sync_mcp.models import (
    ChangeCreate,
    HubSettingsUpdate,
    ProjectCreate,
    ProjectUpdate,
    PublishResult,
    SnapshotImport,
    SnapshotResult,
    Team,
)
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.openapi_diff import endpoints_and_fingerprint
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import ProjectNotFoundError


def create_api_router(
    store: StateStore,
    notifier: ChangeNotifier,
    settings: Settings,
    autosync: AutoSyncService | None = None,
) -> APIRouter:
    router = APIRouter()

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.token:
            return
        expected = f"Bearer {settings.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")

    def _not_found(exc: ProjectNotFoundError) -> HTTPException:
        return HTTPException(status_code=404, detail=f"Project not found: {exc}")

    def _autosync(request: Request) -> AutoSyncService | None:
        return autosync or getattr(request.app.state, "autosync", None)

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/settings")
    async def get_settings():
        return await store.get_hub_settings()

    @router.put("/settings", dependencies=[Depends(require_token)])
    async def put_settings(body: HubSettingsUpdate, request: Request):
        updated = await store.update_hub_settings(body)
        service = _autosync(request)
        if service is not None:
            service.notify_settings_changed()
        return updated

    @router.get("/projects")
    async def list_projects():
        return await store.list_projects()

    @router.post("/projects", dependencies=[Depends(require_token)])
    async def create_project(body: ProjectCreate, request: Request):
        project = await store.create_project(
            body.name,
            body.description,
            openapi_url=body.openapi_url,
            auto_sync=body.auto_sync,
            sync_mode=body.sync_mode.value,
            git_repo_path=body.git_repo_path,
        )
        service = _autosync(request)
        if service is not None and project.openapi_url and project.auto_sync:
            service.notify_settings_changed()
        return project

    @router.get("/projects/{project_id}")
    async def get_project(project_id: str):
        project = await store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        summaries = await store.list_projects()
        for summary in summaries:
            if summary.id == project_id:
                return summary
        return project

    @router.patch("/projects/{project_id}", dependencies=[Depends(require_token)])
    async def patch_project(project_id: str, body: ProjectUpdate, request: Request):
        try:
            project = await store.update_project(project_id, body)
        except ProjectNotFoundError as exc:
            raise _not_found(exc) from exc
        service = _autosync(request)
        if service is not None:
            service.notify_settings_changed()
        return project

    @router.post("/projects/{project_id}/sync", dependencies=[Depends(require_token)])
    async def sync_now(project_id: str, request: Request):
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

    @router.post("/projects/{project_id}/hooks/commit", dependencies=[Depends(require_token)])
    async def commit_hook(project_id: str, request: Request):
        """Webhook surface for remote git/CI: runs the same OpenAPI sync as local on_commit watch."""
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
        changed = await service.sync_project(
            project,
            trigger="commit_hook",
            commit_sha=commit_sha,
        )
        refreshed = await store.get_project(project_id)
        return {"changed": changed, "project": refreshed, "commit_sha": commit_sha}

    @router.get("/projects/{project_id}/state")
    async def project_state(project_id: str):
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

    @router.post("/projects/{project_id}/updates", dependencies=[Depends(require_token)])
    async def publish(project_id: str, change: ChangeCreate) -> PublishResult:
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

    @router.post("/projects/{project_id}/snapshot", dependencies=[Depends(require_token)])
    async def import_snapshot(project_id: str, snapshot: SnapshotImport) -> SnapshotResult:
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

    @router.post("/projects/{project_id}/openapi", dependencies=[Depends(require_token)])
    async def import_openapi(project_id: str, body: dict, request: Request):
        """Import routes from OpenAPI JSON body: {openapi|spec, openapi_url?, team?, notes?}."""
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
    async def state(project_id: str | None = None):
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
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
    ):
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
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

    @router.post("/updates", dependencies=[Depends(require_token)])
    async def publish_legacy(change: ChangeCreate, project_id: str | None = None) -> PublishResult:
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id query parameter is required")
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
    async def events():
        return StreamingResponse(notifier.stream(), media_type="text/event-stream")

    return router
