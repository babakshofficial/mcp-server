from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from uuid import uuid4

from sync_mcp.models import (
    ApiKeyRecord,
    Change,
    ChangeCreate,
    ChangeType,
    HubRole,
    HubSettings,
    HubSettingsUpdate,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectState,
    ProjectSummary,
    ProjectUpdate,
    SnapshotImport,
    SubprojectRecord,
    SubprojectStatus,
    SyncMode,
    Team,
    User,
    UserPublic,
)
from sync_mcp.state import empty_state, rebuild_state, slugify
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import ProjectNotFoundError, UserNotFoundError, filter_changes


class JSONStateStore(StateStore):
    def __init__(self, path: Path, default_project_name: str) -> None:
        self.path = path
        self.default_project_name = default_project_name
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            await self._write(
                {
                    "projects": {},
                    "hub_settings": HubSettings().model_dump(mode="json"),
                    "users": {},
                    "project_members": [],
                    "api_keys": {},
                }
            )
            return
        payload = await self._read()
        if "projects" not in payload and ("changes" in payload or "state" in payload):
            await self._migrate_legacy(payload)
            return
        changed = False
        if "hub_settings" not in payload:
            payload["hub_settings"] = HubSettings().model_dump(mode="json")
            changed = True
        for key, default in (("users", {}), ("project_members", []), ("api_keys", {})):
            if key not in payload:
                payload[key] = default
                changed = True
        if changed:
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

    async def list_projects_for_user(self, user_id: str, *, is_admin: bool) -> list[ProjectSummary]:
        if is_admin:
            return await self.list_projects()
        payload = await self._read()
        allowed = {
            m["project_id"]
            for m in payload.get("project_members", [])
            if m.get("user_id") == user_id
        }
        return [s for s in await self.list_projects() if s.id in allowed]

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
        owner_user_id: str | None = None,
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
            if owner_user_id:
                members = payload.setdefault("project_members", [])
                members.append(
                    {
                        "project_id": project_id,
                        "user_id": owner_user_id,
                        "role": ProjectRole.owner.value,
                        "created_at": now.isoformat(),
                    }
                )
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

    async def delete_project(self, project_id: str) -> None:
        async with self._lock:
            payload = await self._read()
            if project_id not in payload.get("projects", {}):
                raise ProjectNotFoundError(project_id)
            del payload["projects"][project_id]
            payload["project_members"] = [
                m for m in payload.get("project_members", []) if m.get("project_id") != project_id
            ]
            await self._write(payload)

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

    async def count_users(self) -> int:
        payload = await self._read()
        return len(payload.get("users", {}))

    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        hub_role: HubRole = HubRole.member,
    ) -> User:
        async with self._lock:
            payload = await self._read()
            users = payload.setdefault("users", {})
            user = User(
                id=str(uuid4()),
                username=username.strip(),
                hub_role=hub_role,
                created_at=datetime.now(UTC),
            )
            users[user.id] = {
                **user.model_dump(mode="json"),
                "password_hash": password_hash,
            }
            await self._write(payload)
            return user

    async def get_user(self, user_id: str) -> User | None:
        payload = await self._read()
        raw = payload.get("users", {}).get(user_id)
        return User.model_validate({k: v for k, v in raw.items() if k != "password_hash"}) if raw else None

    async def get_user_by_username(self, username: str) -> User | None:
        payload = await self._read()
        needle = username.strip().lower()
        for raw in payload.get("users", {}).values():
            if str(raw.get("username", "")).lower() == needle:
                return User.model_validate({k: v for k, v in raw.items() if k != "password_hash"})
        return None

    async def get_user_password_hash(self, user_id: str) -> str | None:
        payload = await self._read()
        raw = payload.get("users", {}).get(user_id)
        return str(raw["password_hash"]) if raw else None

    async def list_users(self) -> list[UserPublic]:
        payload = await self._read()
        users = []
        for raw in payload.get("users", {}).values():
            user = User.model_validate({k: v for k, v in raw.items() if k != "password_hash"})
            users.append(
                UserPublic(
                    id=user.id,
                    username=user.username,
                    hub_role=user.hub_role,
                    disabled=user.disabled,
                    created_at=user.created_at,
                )
            )
        return sorted(users, key=lambda u: u.created_at)

    async def update_user(
        self,
        user_id: str,
        *,
        hub_role: HubRole | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> User:
        async with self._lock:
            payload = await self._read()
            raw = payload.get("users", {}).get(user_id)
            if not raw:
                raise UserNotFoundError(user_id)
            if hub_role is not None:
                raw["hub_role"] = hub_role.value
            if disabled is not None:
                raw["disabled"] = disabled
            if password_hash is not None:
                raw["password_hash"] = password_hash
            await self._write(payload)
            return User.model_validate({k: v for k, v in raw.items() if k != "password_hash"})

    async def list_project_members(self, project_id: str) -> list[ProjectMember]:
        payload = await self._read()
        users = payload.get("users", {})
        members = []
        for m in payload.get("project_members", []):
            if m.get("project_id") != project_id:
                continue
            username = (users.get(m["user_id"]) or {}).get("username", "")
            members.append(
                ProjectMember(
                    project_id=m["project_id"],
                    user_id=m["user_id"],
                    username=username,
                    role=ProjectRole(m["role"]),
                    created_at=datetime.fromisoformat(m["created_at"]),
                )
            )
        return members

    async def set_project_member(
        self,
        project_id: str,
        user_id: str,
        role: ProjectRole,
    ) -> ProjectMember:
        async with self._lock:
            payload = await self._read()
            self._require_item(payload, project_id)
            raw_user = payload.get("users", {}).get(user_id)
            if not raw_user:
                raise UserNotFoundError(user_id)
            now = datetime.now(UTC)
            members = payload.setdefault("project_members", [])
            members = [m for m in members if not (m["project_id"] == project_id and m["user_id"] == user_id)]
            members.append(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "role": role.value,
                    "created_at": now.isoformat(),
                }
            )
            payload["project_members"] = members
            await self._write(payload)
            return ProjectMember(
                project_id=project_id,
                user_id=user_id,
                username=raw_user["username"],
                role=role,
                created_at=now,
            )

    async def remove_project_member(self, project_id: str, user_id: str) -> None:
        async with self._lock:
            payload = await self._read()
            payload["project_members"] = [
                m
                for m in payload.get("project_members", [])
                if not (m["project_id"] == project_id and m["user_id"] == user_id)
            ]
            await self._write(payload)

    async def get_project_role(self, project_id: str, user_id: str) -> ProjectRole | None:
        payload = await self._read()
        for m in payload.get("project_members", []):
            if m["project_id"] == project_id and m["user_id"] == user_id:
                return ProjectRole(m["role"])
        return None

    async def create_api_key(
        self,
        user_id: str,
        name: str,
        prefix: str,
        key_hash: str,
        *,
        key_id: str | None = None,
    ) -> ApiKeyRecord:
        async with self._lock:
            payload = await self._read()
            record = ApiKeyRecord(
                id=key_id or str(uuid4()),
                user_id=user_id,
                name=name,
                prefix=prefix,
                created_at=datetime.now(UTC),
            )
            keys = payload.setdefault("api_keys", {})
            keys[record.id] = {**record.model_dump(mode="json"), "key_hash": key_hash}
            await self._write(payload)
            return record

    async def list_api_keys(self, user_id: str | None = None) -> list[ApiKeyRecord]:
        payload = await self._read()
        keys = []
        for raw in payload.get("api_keys", {}).values():
            if user_id and raw.get("user_id") != user_id:
                continue
            keys.append(ApiKeyRecord.model_validate({k: v for k, v in raw.items() if k != "key_hash"}))
        return sorted(keys, key=lambda k: k.created_at, reverse=True)

    async def revoke_api_key(self, key_id: str) -> None:
        async with self._lock:
            payload = await self._read()
            key = payload.get("api_keys", {}).get(key_id)
            if key:
                key["revoked_at"] = datetime.now(UTC).isoformat()
                await self._write(payload)

    async def find_api_key_by_prefix(self, prefix: str) -> tuple[ApiKeyRecord, str] | None:
        payload = await self._read()
        for raw in payload.get("api_keys", {}).values():
            if raw.get("prefix") == prefix and not raw.get("revoked_at"):
                record = ApiKeyRecord.model_validate({k: v for k, v in raw.items() if k != "key_hash"})
                return record, str(raw["key_hash"])
        return None

    async def touch_api_key(self, key_id: str) -> None:
        async with self._lock:
            payload = await self._read()
            key = payload.get("api_keys", {}).get(key_id)
            if key:
                key["last_used_at"] = datetime.now(UTC).isoformat()
                await self._write(payload)

    async def count_api_keys(self) -> int:
        payload = await self._read()
        return sum(1 for k in payload.get("api_keys", {}).values() if not k.get("revoked_at"))

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
