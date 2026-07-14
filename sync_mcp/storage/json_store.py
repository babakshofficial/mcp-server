from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sync_mcp.models import (
    Change,
    ChangeCreate,
    ChangeType,
    HubSettings,
    HubSettingsUpdate,
    Project,
    ProjectState,
    ProjectSummary,
    ProjectUpdate,
    SnapshotImport,
    SubprojectRecord,
    SubprojectStatus,
    SyncMode,
    Team,
)
from sync_mcp.state import empty_state, rebuild_state, slugify
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import ProjectNotFoundError, filter_changes


class JSONStateStore(StateStore):
    def __init__(self, path: Path, default_project_name: str) -> None:
        self.path = path
        self.default_project_name = default_project_name
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            await self._write({"projects": {}, "hub_settings": HubSettings().model_dump(mode="json")})
            return
        payload = await self._read()
        if "projects" not in payload and ("changes" in payload or "state" in payload):
            await self._migrate_legacy(payload)
            return
        if "hub_settings" not in payload:
            payload["hub_settings"] = HubSettings().model_dump(mode="json")
            await self._write(payload)

    async def list_projects(self) -> list[ProjectSummary]:
        payload = await self._read()
        summaries: list[ProjectSummary] = []
        for item in payload.get("projects", {}).values():
            project = Project.model_validate(item["meta"])
            state = self._state_from_item(project, item)
            summaries.append(
                ProjectSummary(
                    id=project.id,
                    name=project.name,
                    description=project.description,
                    version=state.version,
                    updated_at=project.updated_at,
                    open_requirements=sum(1 for req in state.requirements if req.status == "open"),
                    api_count=len(state.api),
                    component_count=len(state.components),
                    subprojects=state.subprojects,
                    recent_digest=state.recent_digest,
                    openapi_url=project.openapi_url,
                    auto_sync=project.auto_sync,
                    sync_mode=project.sync_mode,
                    git_repo_path=project.git_repo_path,
                    last_git_sha=project.last_git_sha,
                    last_sync_at=project.last_sync_at,
                    last_sync_status=project.last_sync_status,
                    last_sync_error=project.last_sync_error,
                )
            )
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    async def get_project(self, project_id: str) -> Project | None:
        payload = await self._read()
        item = payload.get("projects", {}).get(project_id)
        return Project.model_validate(item["meta"]) if item else None

    async def create_project(
        self,
        name: str,
        description: str = "",
        *,
        openapi_url: str = "",
        auto_sync: bool = True,
        sync_mode: str = "interval",
        git_repo_path: str = "",
    ) -> Project:
        async with self._lock:
            payload = await self._read()
            projects = payload.setdefault("projects", {})
            project_id = self._unique_slug(name, projects)
            now = datetime.now(UTC)
            project = Project(
                id=project_id,
                name=name,
                description=description,
                created_at=now,
                updated_at=now,
                openapi_url=openapi_url,
                auto_sync=auto_sync,
                sync_mode=SyncMode(sync_mode),
                git_repo_path=git_repo_path,
            )
            projects[project_id] = {
                "meta": project.model_dump(mode="json"),
                "changes": [],
                "subprojects": [],
                "state": empty_state(name, project_id).model_dump(mode="json"),
            }
            await self._write(payload)
            return project

    async def update_project(self, project_id: str, update: ProjectUpdate) -> Project:
        async with self._lock:
            payload = await self._read()
            item = self._require_item(payload, project_id)
            project = Project.model_validate(item["meta"])
            data = project.model_dump(mode="json")
            if update.name is not None:
                data["name"] = update.name
            if update.description is not None:
                data["description"] = update.description
            if update.openapi_url is not None:
                if update.openapi_url != project.openapi_url:
                    data["openapi_fingerprint"] = ""
                data["openapi_url"] = update.openapi_url
            if update.auto_sync is not None:
                data["auto_sync"] = update.auto_sync
            if update.sync_mode is not None:
                if update.sync_mode != project.sync_mode:
                    data["last_git_sha"] = ""
                data["sync_mode"] = update.sync_mode.value
            if update.git_repo_path is not None:
                if update.git_repo_path != project.git_repo_path:
                    data["last_git_sha"] = ""
                data["git_repo_path"] = update.git_repo_path
            data["updated_at"] = datetime.now(UTC).isoformat()
            project = Project.model_validate(data)
            item["meta"] = project.model_dump(mode="json")
            await self._write(payload)
            return project

    async def find_project_by_name_or_id(self, name_or_id: str) -> Project | None:
        payload = await self._read()
        projects = payload.get("projects", {})
        if name_or_id in projects:
            return Project.model_validate(projects[name_or_id]["meta"])
        slug = slugify(name_or_id)
        if slug in projects:
            return Project.model_validate(projects[slug]["meta"])
        for item in projects.values():
            project = Project.model_validate(item["meta"])
            if project.name.lower() == name_or_id.lower():
                return project
        return None

    async def list_auto_sync_targets(self) -> list[Project]:
        payload = await self._read()
        projects = []
        for item in payload.get("projects", {}).values():
            project = Project.model_validate(item["meta"])
            if project.auto_sync and project.openapi_url:
                projects.append(project)
        return projects

    async def update_sync_status(
        self,
        project_id: str,
        *,
        status: str,
        error: str = "",
        fingerprint: str | None = None,
        last_git_sha: str | None = None,
    ) -> None:
        async with self._lock:
            payload = await self._read()
            item = self._require_item(payload, project_id)
            project = Project.model_validate(item["meta"])
            data = project.model_dump(mode="json")
            data["last_sync_at"] = datetime.now(UTC).isoformat()
            data["last_sync_status"] = status
            data["last_sync_error"] = error
            if fingerprint is not None:
                data["openapi_fingerprint"] = fingerprint
            if last_git_sha is not None:
                data["last_git_sha"] = last_git_sha
            item["meta"] = data
            await self._write(payload)

    async def get_hub_settings(self) -> HubSettings:
        payload = await self._read()
        return HubSettings.model_validate(payload.get("hub_settings") or {})

    async def update_hub_settings(self, update: HubSettingsUpdate) -> HubSettings:
        async with self._lock:
            payload = await self._read()
            current = HubSettings.model_validate(payload.get("hub_settings") or {})
            next_settings = HubSettings(
                poll_interval_seconds=(
                    update.poll_interval_seconds
                    if update.poll_interval_seconds is not None
                    else current.poll_interval_seconds
                ),
                auto_sync_enabled=(
                    update.auto_sync_enabled if update.auto_sync_enabled is not None else current.auto_sync_enabled
                ),
            )
            payload["hub_settings"] = next_settings.model_dump(mode="json")
            await self._write(payload)
            return next_settings

    async def publish(self, project_id: str, change: ChangeCreate) -> tuple[Change, ProjectState]:
        async with self._lock:
            payload = await self._read()
            item = self._require_item(payload, project_id)
            project = Project.model_validate(item["meta"])
            changes = [Change.model_validate(c) for c in item.get("changes", [])]
            saved = Change(
                project_id=project_id,
                version=max((c.version for c in changes), default=0) + 1,
                team=change.team,
                type=change.type,
                description=change.description,
                details=change.details,
            )
            changes.append(saved)
            state = self._rebuild(project, item, changes)
            item["changes"] = [c.model_dump(mode="json") for c in changes]
            item["state"] = state.model_dump(mode="json")
            item["meta"]["updated_at"] = datetime.now(UTC).isoformat()
            await self._write(payload)
            return saved, state

    async def get_state(self, project_id: str) -> ProjectState:
        payload = await self._read()
        item = self._require_item(payload, project_id)
        project = Project.model_validate(item["meta"])
        return self._state_from_item(project, item)

    async def get_changelog(
        self,
        project_id: str,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        payload = await self._read()
        item = self._require_item(payload, project_id)
        changes = [Change.model_validate(c) for c in item.get("changes", [])]
        filtered = filter_changes(changes, since=since, team=team, change_type=change_type)
        return sorted(filtered, key=lambda item: item.version, reverse=True)[:limit]

    async def import_snapshot(
        self,
        project_id: str,
        snapshot: SnapshotImport,
    ) -> tuple[Change, ProjectState]:
        async with self._lock:
            payload = await self._read()
            item = self._require_item(payload, project_id)
            project = Project.model_validate(item["meta"])
            changes = [Change.model_validate(c) for c in item.get("changes", [])]
            version = max((c.version for c in changes), default=0)

            def add(change: ChangeCreate) -> None:
                nonlocal version
                version += 1
                changes.append(
                    Change(
                        project_id=project_id,
                        version=version,
                        team=change.team,
                        type=change.type,
                        description=change.description,
                        details=change.details,
                    )
                )

            for endpoint in snapshot.api:
                add(
                    ChangeCreate(
                        team=snapshot.team,
                        type=ChangeType.api_added,
                        description=f"{endpoint.method} {endpoint.path}",
                        details={
                            "method": endpoint.method,
                            "path": endpoint.path,
                            "description": endpoint.description,
                            **endpoint.details,
                        },
                    )
                )
            for component in snapshot.components:
                add(
                    ChangeCreate(
                        team=snapshot.team,
                        type=ChangeType.component_spec,
                        description=component.name,
                        details={"name": component.name, "spec": component.spec, **component.details},
                    )
                )
            for requirement in snapshot.requirements:
                add(
                    ChangeCreate(
                        team=snapshot.team,
                        type=ChangeType.requirement_added,
                        description=requirement.title,
                        details={
                            "id": requirement.id,
                            "title": requirement.title,
                            "description": requirement.description,
                            "status": requirement.status,
                            **requirement.details,
                        },
                    )
                )

            notes = snapshot.notes.strip() or "Initial codebase snapshot"
            summary_bits = [
                f"{len(snapshot.api)} APIs",
                f"{len(snapshot.components)} components",
                f"{len(snapshot.requirements)} requirements",
            ]
            version += 1
            digest = Change(
                project_id=project_id,
                version=version,
                team=snapshot.team,
                type=ChangeType.changelog,
                description=f"{snapshot.team.value} onboarded: {', '.join(summary_bits)}. {notes}",
                details={"source": "import_snapshot", "notes": notes},
            )
            changes.append(digest)

            subprojects = [SubprojectRecord.model_validate(s) for s in item.get("subprojects", [])]
            record = SubprojectRecord(
                team=snapshot.team,
                status=SubprojectStatus.ready,
                summary=notes,
                onboarded_at=datetime.now(UTC),
            )
            subprojects = [s for s in subprojects if s.team != snapshot.team] + [record]
            item["subprojects"] = [s.model_dump(mode="json") for s in subprojects]
            state = self._rebuild(project, item, changes)
            item["changes"] = [c.model_dump(mode="json") for c in changes]
            item["state"] = state.model_dump(mode="json")
            item["meta"]["updated_at"] = datetime.now(UTC).isoformat()
            await self._write(payload)
            return digest, state

    async def mark_subproject(
        self,
        project_id: str,
        team: Team,
        status: SubprojectStatus,
        summary: str = "",
    ) -> SubprojectRecord:
        async with self._lock:
            payload = await self._read()
            item = self._require_item(payload, project_id)
            project = Project.model_validate(item["meta"])
            record = SubprojectRecord(
                team=team,
                status=status,
                summary=summary,
                onboarded_at=datetime.now(UTC) if status == SubprojectStatus.ready else None,
            )
            subprojects = [SubprojectRecord.model_validate(s) for s in item.get("subprojects", [])]
            subprojects = [s for s in subprojects if s.team != team] + [record]
            item["subprojects"] = [s.model_dump(mode="json") for s in subprojects]
            changes = [Change.model_validate(c) for c in item.get("changes", [])]
            state = self._rebuild(project, item, changes)
            item["state"] = state.model_dump(mode="json")
            item["meta"]["updated_at"] = datetime.now(UTC).isoformat()
            await self._write(payload)
            return record

    async def get_subprojects(self, project_id: str) -> list[SubprojectRecord]:
        payload = await self._read()
        item = self._require_item(payload, project_id)
        return [SubprojectRecord.model_validate(s) for s in item.get("subprojects", [])]

    async def _migrate_legacy(self, payload: dict) -> None:
        project_id = slugify(self.default_project_name)
        now = datetime.now(UTC)
        project = Project(id=project_id, name=self.default_project_name, created_at=now, updated_at=now)
        changes = [Change.model_validate({**c, "project_id": project_id}) for c in payload.get("changes", [])]
        state_data = payload.get("state")
        if state_data:
            state = ProjectState.model_validate(state_data)
            state.project_id = project_id
            state.project = self.default_project_name
        else:
            state = rebuild_state(self.default_project_name, changes, project_id=project_id)
        await self._write(
            {
                "projects": {
                    project_id: {
                        "meta": project.model_dump(mode="json"),
                        "changes": [c.model_dump(mode="json") for c in changes],
                        "subprojects": [],
                        "state": state.model_dump(mode="json"),
                    }
                }
            }
        )

    def _require_item(self, payload: dict, project_id: str) -> dict:
        item = payload.get("projects", {}).get(project_id)
        if not item:
            raise ProjectNotFoundError(project_id)
        return item

    def _unique_slug(self, name: str, projects: dict) -> str:
        base = slugify(name)
        candidate = base
        suffix = 2
        while candidate in projects:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _state_from_item(self, project: Project, item: dict) -> ProjectState:
        subprojects = [SubprojectRecord.model_validate(s) for s in item.get("subprojects", [])]
        if item.get("state"):
            state = ProjectState.model_validate(item["state"])
            state.project_id = project.id
            state.project = project.name
            state.subprojects = subprojects
            return state
        changes = [Change.model_validate(c) for c in item.get("changes", [])]
        return rebuild_state(project.name, changes, project_id=project.id, subprojects=subprojects)

    def _rebuild(self, project: Project, item: dict, changes: list[Change]) -> ProjectState:
        subprojects = [SubprojectRecord.model_validate(s) for s in item.get("subprojects", [])]
        return rebuild_state(project.name, changes, project_id=project.id, subprojects=subprojects)

    async def _read(self) -> dict:
        return await asyncio.to_thread(lambda: json.loads(self.path.read_text() or "{}"))

    async def _write(self, payload: dict) -> None:
        await asyncio.to_thread(lambda: self.path.write_text(json.dumps(payload, indent=2)))
