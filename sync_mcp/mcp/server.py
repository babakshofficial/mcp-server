from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from sync_mcp.auth import rbac
from sync_mcp.config import Settings
from sync_mcp.http_proxy import async_client_for
from sync_mcp.models import (
    ApiEndpoint,
    Artifact,
    ArtifactImport,
    ChangeAckCreate,
    ChangeCreate,
    ChangeType,
    ComponentSpec,
    ProjectRole,
    ProjectUpdate,
    PublishResult,
    Requirement,
    SnapshotImport,
    SnapshotResult,
    Team,
    Teams,
    normalize_team,
)
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.onboarding import onboard_checklist, onboard_instructions
from sync_mcp.openapi_diff import endpoints_and_fingerprint
from sync_mcp.project_context import get_principal_context, get_project_context
from sync_mcp.state import format_state_markdown
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import ProjectNotFoundError


def create_mcp_server(store: StateStore, notifier: ChangeNotifier, settings: Settings) -> FastMCP:
    # streamable_http_path="/" so mounting at /mcp yields POST /mcp (not /mcp/mcp).
    # Disable DNS-rebinding guards for self-hosted LAN/team hubs.
    mcp = FastMCP(
        "team-sync",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def _project_error(exc: ProjectNotFoundError) -> dict[str, str]:
        return {"error": "project_not_found", "project_id": str(exc)}

    def _forbidden(detail: str = "Insufficient permission") -> dict[str, str]:
        return {"error": "forbidden", "detail": detail}

    def require_principal():
        principal = get_principal_context()
        if principal is None:
            raise PermissionError("Authentication required")
        return principal

    async def require_project_role(project_id: str, minimum: ProjectRole):
        principal = require_principal()
        if principal.is_admin:
            return principal
        role = await store.get_project_role(project_id, principal.user_id)
        if not rbac.role_at_least(role, minimum):
            raise PermissionError("Insufficient project permission")
        return principal

    def resolve_project_id(explicit: str | None = None) -> str:
        ctx = get_project_context()
        if ctx is not None:
            return ctx.project_id
        if explicit:
            return explicit
        raise ProjectNotFoundError("project_id required (or set Project header)")

    def resolve_team(explicit: str | None = None) -> Team:
        if explicit:
            return normalize_team(explicit)
        ctx = get_project_context()
        if ctx is not None:
            return ctx.team
        raise ValueError("team is required when Project header is not set")

    @mcp.tool()
    async def list_projects() -> list[dict[str, Any]] | dict[str, str]:
        """List projects visible to the authenticated user."""
        try:
            principal = require_principal()
        except PermissionError as exc:
            return _forbidden(str(exc))
        projects = await store.list_projects_for_user(principal.user_id, is_admin=principal.is_admin)
        return [project.model_dump(mode="json") for project in projects]

    @mcp.tool()
    async def register_project(
        name: str,
        description: str = "",
        template: str = "web",
        teams: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a project (or return the existing one with the same slug)."""
        try:
            principal = require_principal()
            if not rbac.can_create_project(principal):
                return _forbidden("Cannot create projects")
        except PermissionError as exc:
            return _forbidden(str(exc))
        from sync_mcp.models import PROJECT_TEMPLATES, resolve_project_teams
        from sync_mcp.state import slugify

        if template not in PROJECT_TEMPLATES:
            return {"error": f"template must be one of: {', '.join(sorted(PROJECT_TEMPLATES))}"}
        team_list = resolve_project_teams(template=template, teams=teams)
        existing = await store.list_projects_for_user(principal.user_id, is_admin=True)

        slug = slugify(name)
        for project in existing:
            if project.id == slug or project.name.lower() == name.lower():
                return {"created": False, "project": project.model_dump(mode="json")}
        all_projects = await store.list_projects()
        for project in all_projects:
            if project.id == slug or project.name.lower() == name.lower():
                return {"created": False, "project": project.model_dump(mode="json")}
        project = await store.create_project(
            name,
            description,
            owner_user_id=principal.user_id,
            teams=team_list,
        )
        return {"created": True, "project": project.model_dump(mode="json")}

    @mcp.tool()
    async def onboard_subproject(project_id: str | None = None, team: str | None = None) -> dict[str, Any]:
        """Start Cursor-driven onboarding for a team subproject; returns scan checklist."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.viewer)
            team_enum = resolve_team(team)
            project = await store.get_project(resolved_id)
            if project is None:
                raise ProjectNotFoundError(resolved_id)
            subprojects = await store.get_subprojects(resolved_id)
            current = next((item for item in subprojects if item.team == team_enum), None)
            return {
                "project": project.model_dump(mode="json"),
                "team": team_enum.value,
                "already_onboarded": bool(current and current.status.value == "ready"),
                "subproject": current.model_dump(mode="json") if current else None,
                "checklist": onboard_checklist(team_enum),
                "instructions": onboard_instructions(resolved_id, team_enum),
                "next_tool": "import_openapi" if team_enum == Teams.backend else "import_snapshot",
                "openapi_hint": (
                    "For FastAPI, call import_openapi with openapi_url like http://localhost:8000/openapi.json"
                    if team_enum == Teams.backend
                    else None
                ),
            }
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}

    @mcp.tool()
    async def import_snapshot(
        project_id: str | None = None,
        team: str | None = None,
        api: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        requirements: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        notes: str = "",
        replace: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Bulk-publish discovered APIs/components/requirements after codebase review.

        Set replace=true to prune team-owned items missing from this snapshot.
        """
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.editor)
            team_enum = resolve_team(team)
            snapshot = SnapshotImport(
                team=team_enum,
                api=[ApiEndpoint.model_validate(item) for item in (api or [])],
                components=[ComponentSpec.model_validate(item) for item in (components or [])],
                requirements=[Requirement.model_validate(item) for item in (requirements or [])],
                artifacts=[Artifact.model_validate(item) for item in (artifacts or [])],
                notes=notes,
                replace=replace,
            )
            saved, next_state = await store.import_snapshot(resolved_id, snapshot)
            await notifier.publish(saved, next_state)
            if ctx is not None:
                await _notify_mcp_resources(ctx, resolved_id)
            return SnapshotResult(
                project_id=resolved_id,
                team=snapshot.team,
                change_id=saved.id,
                version=saved.version,
                state=next_state,
            ).model_dump(mode="json")
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}

    @mcp.tool()
    async def import_artifacts(
        project_id: str | None = None,
        team: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        notes: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Upsert typed artifacts (env vars, flags, events, tokens, etc.)."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.editor)
            team_enum = resolve_team(team)
            body = ArtifactImport(
                team=team_enum,
                artifacts=[Artifact.model_validate(item) for item in (artifacts or [])],
                notes=notes,
            )
            last = None
            next_state = await store.get_state(resolved_id)
            for artifact in body.artifacts:
                change = ChangeCreate(
                    team=body.team,
                    type=ChangeType.artifact_upsert,
                    description=artifact.title or artifact.key,
                    details={
                        "kind": artifact.kind.value,
                        "key": artifact.key,
                        "title": artifact.title,
                        "description": artifact.description,
                        **artifact.details,
                    },
                )
                last, next_state = await store.publish(resolved_id, change)
            if notes.strip():
                last, next_state = await store.publish(
                    resolved_id,
                    ChangeCreate(
                        team=body.team,
                        type=ChangeType.changelog,
                        description=notes.strip(),
                        details={"source": "import_artifacts", "count": len(body.artifacts)},
                    ),
                )
            if last is not None:
                await notifier.publish(last, next_state)
                if ctx is not None:
                    await _notify_mcp_resources(ctx, resolved_id)
            return {"imported": len(body.artifacts), "state": next_state.model_dump(mode="json")}
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}

    @mcp.tool()
    async def acknowledge_change(
        change_id: str,
        status: str,
        project_id: str | None = None,
        team: str | None = None,
        note: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Acknowledge a changelog entry (ack | blocked | needs_version)."""
        try:
            resolved_id = resolve_project_id(project_id)
            principal = await require_project_role(resolved_id, ProjectRole.viewer)
            team_enum = resolve_team(team)
            body = ChangeAckCreate(change_id=change_id, team=team_enum, status=status, note=note)
            saved, next_state = await store.publish(
                resolved_id,
                ChangeCreate(
                    team=body.team,
                    type=ChangeType.change_ack,
                    description=f"{body.status.value} change {body.change_id}",
                    details={
                        "change_id": body.change_id,
                        "status": body.status.value,
                        "note": body.note,
                        "user_id": principal.user_id,
                        "username": principal.username,
                    },
                ),
            )
            await notifier.publish(saved, next_state)
            if ctx is not None:
                await _notify_mcp_resources(ctx, resolved_id)
            return {"ack": saved.model_dump(mode="json"), "state": next_state.model_dump(mode="json")}
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}

    @mcp.tool()
    async def get_artifacts(project_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        """List typed artifacts for a project, optionally filtered by kind."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.viewer)
            state = await store.get_state(resolved_id)
            items = state.artifacts
            if kind:
                items = [a for a in items if a.kind.value == kind]
            return {"project_id": resolved_id, "artifacts": [a.model_dump(mode="json") for a in items]}
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))

    @mcp.tool()
    async def import_openapi(
        project_id: str | None = None,
        openapi_url: str | None = None,
        openapi_json: dict[str, Any] | None = None,
        team: str | None = None,
        notes: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Import FastAPI/OpenAPI routes into a project from a URL or pasted OpenAPI JSON."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.editor)
            team_enum = resolve_team(team) if team is not None or get_project_context() else Teams.backend
            if openapi_json is None:
                if not openapi_url:
                    return {"error": "provide openapi_url or openapi_json"}
                async with async_client_for(openapi_url, timeout=30.0) as client:
                    response = await client.get(openapi_url)
                    response.raise_for_status()
                    openapi_json = response.json()
            endpoints, fingerprint = endpoints_and_fingerprint(openapi_json, team=team_enum)
            note = notes.strip() or f"Imported {len(endpoints)} endpoints from OpenAPI"
            if openapi_url:
                note = f"{note} ({openapi_url})"
            snapshot = SnapshotImport(team=team_enum, api=endpoints, notes=note)
            saved, next_state = await store.import_snapshot(resolved_id, snapshot)
            if openapi_url:
                await store.update_project(
                    resolved_id,
                    ProjectUpdate(openapi_url=openapi_url, auto_sync=True),
                )
                await store.update_sync_status(
                    resolved_id,
                    status="updated",
                    error="",
                    fingerprint=fingerprint,
                )
            await notifier.publish(saved, next_state)
            if ctx is not None:
                await _notify_mcp_resources(ctx, resolved_id)
            return {
                "imported_endpoints": len(endpoints),
                "auto_sync_enabled": bool(openapi_url),
                "openapi_url": openapi_url,
                "result": SnapshotResult(
                    project_id=resolved_id,
                    team=snapshot.team,
                    change_id=saved.id,
                    version=saved.version,
                    state=next_state,
                ).model_dump(mode="json"),
            }
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface fetch/parse errors to Cursor
            return {"error": "openapi_import_failed", "detail": str(exc)}

    @mcp.tool()
    async def publish_update(
        project_id: str | None = None,
        team: str | None = None,
        type: str = "other",
        description: str = "",
        details: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Publish a frontend/backend change into a project shared state."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.editor)
            team_enum = resolve_team(team)
            saved, next_state = await store.publish(
                resolved_id,
                ChangeCreate(
                    team=team_enum,
                    type=type,
                    description=description,
                    details=details or {},
                ),
            )
            await notifier.publish(saved, next_state)
            if ctx is not None:
                await _notify_mcp_resources(ctx, resolved_id)
            return PublishResult(
                change_id=saved.id,
                project_id=resolved_id,
                version=saved.version,
                state=next_state,
            ).model_dump(mode="json")
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ValueError as exc:
            return {"error": "invalid_arguments", "detail": str(exc)}

    @mcp.tool()
    async def get_latest_state(project_id: str | None = None) -> dict[str, Any]:
        """Return the full aggregated state plus a Cursor-friendly digest."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.viewer)
            state = await store.get_state(resolved_id)
            payload = state.model_dump(mode="json")
            payload["digest_markdown"] = format_state_markdown(state)
            return payload
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ProjectNotFoundError as exc:
            return _project_error(exc)

    @mcp.tool()
    async def get_changelog(
        project_id: str | None = None,
        since: str | None = None,
        team: str | None = None,
        type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]] | dict[str, str]:
        """Return changes since an ISO timestamp or version for a project."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.viewer)
            resolved_team = team
            if resolved_team is None:
                ctx = get_project_context()
                if ctx is not None:
                    resolved_team = ctx.team
            changes = await store.get_changelog(
                resolved_id,
                since=since,
                team=resolved_team,
                change_type=type,
                limit=limit,
            )
            return [change.model_dump(mode="json") for change in changes]
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ProjectNotFoundError as exc:
            return _project_error(exc)

    @mcp.tool()
    async def subscribe_to_changes(project_id: str | None = None) -> dict[str, str]:
        """Describe subscription resources clients can watch for near-real-time updates."""
        try:
            resolved_id = resolve_project_id(project_id)
            await require_project_role(resolved_id, ProjectRole.viewer)
        except PermissionError as exc:
            return _forbidden(str(exc))
        except ProjectNotFoundError as exc:
            return _project_error(exc)
        return {
            "state_resource": f"sync://projects/{resolved_id}/state",
            "changelog_resource": f"sync://projects/{resolved_id}/changelog",
            "projects_resource": "sync://projects",
            "dashboard_events": "/api/events",
            "note": "Subscribe to sync://projects/{id}/state when your MCP client supports resource updates.",
        }

    @mcp.resource("sync://projects")
    async def projects_resource() -> str:
        """Projects visible to the authenticated principal."""
        principal = get_principal_context()
        if principal is None:
            return json.dumps({"error": "Authentication required"})
        projects = await store.list_projects_for_user(principal.user_id, is_admin=principal.is_admin)
        return "[" + ",".join(project.model_dump_json(indent=2) for project in projects) + "]"

    @mcp.resource("sync://projects/{project_id}/state")
    async def state_resource(project_id: str) -> str:
        """Current aggregated state for a project (membership required)."""
        try:
            await require_project_role(project_id, ProjectRole.viewer)
            return (await store.get_state(project_id)).model_dump_json(indent=2)
        except PermissionError as exc:
            return json.dumps(_forbidden(str(exc)))
        except ProjectNotFoundError as exc:
            return json.dumps(_project_error(exc))

    @mcp.resource("sync://projects/{project_id}/changelog")
    async def changelog_resource(project_id: str) -> str:
        """Recent changelog for a project (membership required)."""
        try:
            await require_project_role(project_id, ProjectRole.viewer)
            changes = await store.get_changelog(project_id, limit=50)
            return "[" + ",".join(change.model_dump_json(indent=2) for change in changes) + "]"
        except PermissionError as exc:
            return json.dumps(_forbidden(str(exc)))
        except ProjectNotFoundError as exc:
            return json.dumps(_project_error(exc))

    @mcp.prompt()
    async def onboard_subproject_prompt(project_id: str | None = None, team: str | None = None) -> str:
        """Prompt Cursor to review the open workspace and import a subproject snapshot."""
        try:
            resolved_id = resolve_project_id(project_id)
            team_enum = resolve_team(team)
            return onboard_instructions(resolved_id, team_enum)
        except (ProjectNotFoundError, ValueError) as exc:
            return str(exc)

    @mcp.prompt()
    async def sync_digest(project_id: str | None = None, team: str | None = None) -> str:
        """Create a markdown digest of the latest shared state for Cursor chat."""
        try:
            resolved_id = resolve_project_id(project_id)
            state = await store.get_state(resolved_id)
            changes = await store.get_changelog(resolved_id, team=team, limit=10)
            lines = [format_state_markdown(state), "", "## Recent changes"]
            lines.extend(f"- [{change.team}] {change.type}: {change.description}" for change in changes)
            return "\n".join(lines)
        except ProjectNotFoundError as exc:
            return f"Project not found: {exc}"

    return mcp


async def _notify_mcp_resources(ctx: Context, project_id: str) -> None:
    try:
        await ctx.session.send_resource_updated("sync://projects")
        await ctx.session.send_resource_updated(f"sync://projects/{project_id}/state")
        await ctx.session.send_resource_updated(f"sync://projects/{project_id}/changelog")
    except Exception:
        await ctx.info("Published update; resource notification was not supported by this client.")
