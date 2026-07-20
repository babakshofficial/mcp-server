from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from uuid import uuid4

import aiosqlite

from sync_mcp.models import (
    ApiKeyRecord,
    Change,
    ChangeCreate,
    ChangeType,
    DEFAULT_PROJECT_TEAMS,
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


class ProjectNotFoundError(LookupError):
    pass


class UserNotFoundError(LookupError):
    pass


class SQLiteStateStore(StateStore):
    def __init__(self, path: Path, default_project_name: str) -> None:
        self.path = path
        self.default_project_name = default_project_name

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await self._ensure_schema(db)
            await self._migrate_legacy(db)
            await self._ensure_project_columns(db)
            await self._ensure_auth_schema(db)
            await self._ensure_subproject_agent_columns(db)
            await self._ensure_hub_settings(db)
            await db.commit()

    async def list_projects(self) -> list[ProjectSummary]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM projects ORDER BY updated_at DESC")
            summaries: list[ProjectSummary] = []
            for row in rows:
                state = await self._load_state(db, row["id"], row["name"])
                summaries.append(self._summary_from_row(row, state))
            return summaries

    async def list_projects_for_user(self, user_id: str, *, is_admin: bool) -> list[ProjectSummary]:
        if is_admin:
            return await self.list_projects()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT p.* FROM projects p
                INNER JOIN project_members m ON m.project_id = p.id
                WHERE m.user_id = ?
                ORDER BY p.updated_at DESC
                """,
                (user_id,),
            )
            summaries: list[ProjectSummary] = []
            for row in rows:
                state = await self._load_state(db, row["id"], row["name"])
                summaries.append(self._summary_from_row(row, state))
            return summaries

    async def get_project(self, project_id: str) -> Project | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM projects WHERE id = ?", (project_id,))
            if not rows:
                return None
            return self._project_from_row(rows[0])

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
        teams: list[str] | None = None,
    ) -> Project:
        from sync_mcp.models import DEFAULT_PROJECT_TEAMS, SubprojectRecord, SubprojectStatus

        project_id = await self._unique_slug(name)
        now = datetime.now(UTC)
        mode = SyncMode(sync_mode)
        team_list = list(teams) if teams is not None else list(DEFAULT_PROJECT_TEAMS)
        project = Project(
            id=project_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            openapi_url=openapi_url,
            auto_sync=auto_sync,
            sync_mode=mode,
            git_repo_path=git_repo_path,
            teams=team_list,
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO projects (
                    id, name, description, created_at, updated_at,
                    openapi_url, auto_sync, sync_mode, git_repo_path, last_git_sha,
                    last_sync_at, last_sync_status, last_sync_error, openapi_fingerprint, teams
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, '', '', '', ?)
                """,
                (
                    project.id,
                    project.name,
                    project.description,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.openapi_url,
                    1 if project.auto_sync else 0,
                    project.sync_mode.value,
                    project.git_repo_path,
                    json.dumps(team_list),
                ),
            )
            subprojects = [
                SubprojectRecord(team=t, status=SubprojectStatus.pending, summary="Awaiting onboarding")
                for t in team_list
            ]
            await db.execute(
                "INSERT INTO aggregated_state (project_id, state) VALUES (?, ?)",
                (
                    project.id,
                    empty_state(project.name, project.id, subprojects=subprojects).model_dump_json(),
                ),
            )
            for record in subprojects:
                await self._upsert_subproject(
                    db,
                    project.id,
                    record.team,
                    SubprojectStatus.pending,
                    summary=record.summary,
                )
            if owner_user_id:
                await db.execute(
                    """
                    INSERT INTO project_members (project_id, user_id, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project.id, owner_user_id, ProjectRole.owner.value, now.isoformat()),
                )
            await db.commit()
        return project

    async def update_project(self, project_id: str, update: ProjectUpdate) -> Project:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            name = update.name if update.name is not None else project.name
            description = update.description if update.description is not None else project.description
            openapi_url = update.openapi_url if update.openapi_url is not None else project.openapi_url
            auto_sync = update.auto_sync if update.auto_sync is not None else project.auto_sync
            sync_mode = update.sync_mode if update.sync_mode is not None else project.sync_mode
            git_repo_path = update.git_repo_path if update.git_repo_path is not None else project.git_repo_path
            teams = update.teams if update.teams is not None else project.teams
            fingerprint = (
                ""
                if update.openapi_url is not None and update.openapi_url != project.openapi_url
                else project.openapi_fingerprint
            )
            last_git_sha = (
                ""
                if (update.git_repo_path is not None and update.git_repo_path != project.git_repo_path)
                or (update.sync_mode is not None and update.sync_mode != project.sync_mode)
                else project.last_git_sha
            )
            now = datetime.now(UTC).isoformat()
            await db.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, openapi_url = ?, auto_sync = ?,
                    sync_mode = ?, git_repo_path = ?, last_git_sha = ?,
                    openapi_fingerprint = ?, teams = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    description,
                    openapi_url,
                    1 if auto_sync else 0,
                    sync_mode.value,
                    git_repo_path,
                    last_git_sha,
                    fingerprint,
                    json.dumps(teams),
                    now,
                    project_id,
                ),
            )
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM projects WHERE id = ?", (project_id,))
            return self._project_from_row(rows[0])

    async def delete_project(self, project_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT 1 FROM projects WHERE id = ?", (project_id,))
            if not rows:
                raise ProjectNotFoundError(project_id)
            await db.execute("DELETE FROM project_members WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM subprojects WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM changes WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM aggregated_state WHERE project_id = ?", (project_id,))
            await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await db.commit()

    async def find_project_by_name_or_id(self, name_or_id: str) -> Project | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM projects WHERE id = ?", (name_or_id,))
            if rows:
                return self._project_from_row(rows[0])
            slug = slugify(name_or_id)
            rows = await db.execute_fetchall(
                "SELECT * FROM projects WHERE id = ? OR lower(name) = lower(?)",
                (slug, name_or_id),
            )
            return self._project_from_row(rows[0]) if rows else None

    async def list_auto_sync_targets(self) -> list[Project]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT * FROM projects
                WHERE auto_sync = 1 AND openapi_url != ''
                ORDER BY id ASC
                """
            )
            return [self._project_from_row(row) for row in rows]

    async def update_sync_status(
        self,
        project_id: str,
        *,
        status: str,
        error: str = "",
        fingerprint: str | None = None,
        last_git_sha: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            now = datetime.now(UTC).isoformat()
            # Build dynamic update to preserve untouched fields.
            fields = ["last_sync_at = ?", "last_sync_status = ?", "last_sync_error = ?"]
            values: list[object] = [now, status, error]
            if fingerprint is not None:
                fields.append("openapi_fingerprint = ?")
                values.append(fingerprint)
            if last_git_sha is not None:
                fields.append("last_git_sha = ?")
                values.append(last_git_sha)
            values.append(project_id)
            await db.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values)
            await db.commit()

    async def count_users(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT COUNT(*) FROM users")
            return int(rows[0][0])

    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        hub_role: HubRole = HubRole.member,
    ) -> User:
        user = User(
            id=str(uuid4()),
            username=username.strip(),
            hub_role=hub_role,
            disabled=False,
            created_at=datetime.now(UTC),
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (id, username, password_hash, hub_role, disabled, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (user.id, user.username, password_hash, user.hub_role.value, user.created_at.isoformat()),
            )
            await db.commit()
        return user

    async def get_user(self, user_id: str) -> User | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM users WHERE id = ?", (user_id,))
            return self._user_from_row(rows[0]) if rows else None

    async def get_user_by_username(self, username: str) -> User | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM users WHERE lower(username) = lower(?)",
                (username.strip(),),
            )
            return self._user_from_row(rows[0]) if rows else None

    async def get_user_password_hash(self, user_id: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT password_hash FROM users WHERE id = ?", (user_id,))
            return str(rows[0][0]) if rows else None

    async def list_users(self) -> list[UserPublic]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM users ORDER BY created_at ASC")
            return [self._user_public_from_row(row) for row in rows]

    async def update_user(
        self,
        user_id: str,
        *,
        hub_role: HubRole | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> User:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM users WHERE id = ?", (user_id,))
            if not rows:
                raise UserNotFoundError(user_id)
            user = self._user_from_row(rows[0])
            next_role = hub_role if hub_role is not None else user.hub_role
            next_disabled = disabled if disabled is not None else user.disabled
            fields = ["hub_role = ?", "disabled = ?"]
            values: list[object] = [next_role.value, 1 if next_disabled else 0]
            if password_hash is not None:
                fields.append("password_hash = ?")
                values.append(password_hash)
            values.append(user_id)
            await db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM users WHERE id = ?", (user_id,))
            return self._user_from_row(rows[0])

    async def list_project_members(self, project_id: str) -> list[ProjectMember]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT m.*, u.username AS username
                FROM project_members m
                LEFT JOIN users u ON u.id = m.user_id
                WHERE m.project_id = ?
                ORDER BY m.created_at ASC
                """,
                (project_id,),
            )
            return [
                ProjectMember(
                    project_id=row["project_id"],
                    user_id=row["user_id"],
                    username=row["username"] or "",
                    role=ProjectRole(row["role"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def set_project_member(
        self,
        project_id: str,
        user_id: str,
        role: ProjectRole,
    ) -> ProjectMember:
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await self._require_project(db, project_id)
            user_rows = await db.execute_fetchall("SELECT * FROM users WHERE id = ?", (user_id,))
            if not user_rows:
                raise UserNotFoundError(user_id)
            await db.execute(
                """
                INSERT INTO project_members (project_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (project_id, user_id, role.value, now.isoformat()),
            )
            await db.commit()
            username = user_rows[0]["username"]
        return ProjectMember(
            project_id=project_id,
            user_id=user_id,
            username=username,
            role=role,
            created_at=now,
        )

    async def remove_project_member(self, project_id: str, user_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )
            await db.commit()

    async def get_project_role(self, project_id: str, user_id: str) -> ProjectRole | None:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall(
                "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )
            if not rows:
                return None
            return ProjectRole(rows[0][0])

    async def create_api_key(
        self,
        user_id: str,
        name: str,
        prefix: str,
        key_hash: str,
        *,
        key_id: str | None = None,
    ) -> ApiKeyRecord:
        record = ApiKeyRecord(
            id=key_id or str(uuid4()),
            user_id=user_id,
            name=name,
            prefix=prefix,
            created_at=datetime.now(UTC),
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO api_keys (id, user_id, name, prefix, key_hash, created_at, last_used_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (record.id, record.user_id, record.name, record.prefix, key_hash, record.created_at.isoformat()),
            )
            await db.commit()
        return record

    async def list_api_keys(self, user_id: str | None = None) -> list[ApiKeyRecord]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if user_id:
                rows = await db.execute_fetchall(
                    "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                rows = await db.execute_fetchall("SELECT * FROM api_keys ORDER BY created_at DESC")
            return [self._api_key_from_row(row) for row in rows]

    async def revoke_api_key(self, key_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), key_id),
            )
            await db.commit()

    async def find_api_key_by_prefix(self, prefix: str) -> tuple[ApiKeyRecord, str] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT * FROM api_keys
                WHERE prefix = ? AND revoked_at IS NULL
                """,
                (prefix,),
            )
            if not rows:
                return None
            row = rows[0]
            return self._api_key_from_row(row), str(row["key_hash"])

    async def touch_api_key(self, key_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), key_id),
            )
            await db.commit()

    async def count_api_keys(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL")
            return int(rows[0][0])

    async def get_hub_settings(self) -> HubSettings:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM hub_settings WHERE id = 1")
            if not rows:
                return HubSettings()
            row = rows[0]
            return HubSettings(
                poll_interval_seconds=int(row["poll_interval_seconds"]),
                auto_sync_enabled=bool(row["auto_sync_enabled"]),
            )

    async def update_hub_settings(self, update: HubSettingsUpdate) -> HubSettings:
        current = await self.get_hub_settings()
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
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO hub_settings (id, poll_interval_seconds, auto_sync_enabled)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    poll_interval_seconds = excluded.poll_interval_seconds,
                    auto_sync_enabled = excluded.auto_sync_enabled
                """,
                (next_settings.poll_interval_seconds, 1 if next_settings.auto_sync_enabled else 0),
            )
            await db.commit()
        return next_settings

    async def publish(self, project_id: str, change: ChangeCreate) -> tuple[Change, ProjectState]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            version = await self._next_version(db, project_id)
            saved = Change(
                project_id=project_id,
                version=version,
                team=change.team,
                type=change.type,
                description=change.description,
                details=change.details,
            )
            await self._insert_change(db, saved)
            state = await self._rebuild_and_save(db, project)
            await db.commit()
            return saved, state

    async def get_state(self, project_id: str) -> ProjectState:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            return await self._load_state(db, project.id, project.name)

    async def get_changelog(
        self,
        project_id: str,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await self._require_project(db, project_id)
            changes = await self._load_changes(db, project_id)
        filtered = filter_changes(changes, since=since, team=team, change_type=change_type)
        return sorted(filtered, key=lambda item: item.version, reverse=True)[:limit]

    async def import_snapshot(
        self,
        project_id: str,
        snapshot: SnapshotImport,
    ) -> tuple[Change, ProjectState]:
        from sync_mcp.snapshot_ops import prune_changes_for_replace, snapshot_to_changes

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            current = await self.get_state(project_id)
            synthetic = prune_changes_for_replace(snapshot=snapshot, current=current)
            synthetic.extend(snapshot_to_changes(snapshot))

            for item in synthetic:
                version = await self._next_version(db, project_id)
                saved_change = Change(
                    project_id=project_id,
                    version=version,
                    team=item.team,
                    type=item.type,
                    description=item.description,
                    details=item.details,
                )
                await self._insert_change(db, saved_change)

            summary_bits = [
                f"{len(snapshot.api)} APIs",
                f"{len(snapshot.components)} components",
                f"{len(snapshot.requirements)} requirements",
                f"{len(snapshot.artifacts)} artifacts",
            ]
            if snapshot.replace:
                summary_bits.append("replace=true")
            notes = snapshot.notes.strip() or "Initial codebase snapshot"
            digest_version = await self._next_version(db, project_id)
            digest = Change(
                project_id=project_id,
                version=digest_version,
                team=snapshot.team,
                type=ChangeType.changelog,
                description=f"{snapshot.team} onboarded: {', '.join(summary_bits)}. {notes}",
                details={
                    "source": "import_snapshot",
                    "notes": notes,
                    "replace": snapshot.replace,
                },
            )
            await self._insert_change(db, digest)

            await self._upsert_subproject(
                db,
                project_id,
                snapshot.team,
                SubprojectStatus.ready,
                summary=notes,
            )
            state = await self._rebuild_and_save(db, project)
            await db.commit()
            return digest, state

    async def mark_subproject(
        self,
        project_id: str,
        team: Team,
        status: SubprojectStatus,
        summary: str = "",
    ) -> SubprojectRecord:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            record = await self._upsert_subproject(db, project_id, team, status, summary)
            await self._rebuild_and_save(db, project)
            await db.commit()
            return record

    async def get_subprojects(self, project_id: str) -> list[SubprojectRecord]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await self._require_project(db, project_id)
            return await self._load_subprojects(db, project_id)

    async def update_agent_status(
        self,
        project_id: str,
        team: Team,
        *,
        status: str,
        error: str = "",
        commit_sha: str = "",
    ) -> SubprojectRecord:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            now = datetime.now(UTC)
            existing = await self._load_subprojects(db, project_id)
            current = next((item for item in existing if item.team == team), None)
            sub_status = current.status if current else SubprojectStatus.pending
            summary = current.summary if current else ""
            onboarded_at = current.onboarded_at if current else None
            await db.execute(
                """
                INSERT INTO subprojects (
                    project_id, team, status, summary, onboarded_at,
                    last_agent_at, last_agent_status, last_agent_error, last_agent_sha
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, team) DO UPDATE SET
                    last_agent_at = excluded.last_agent_at,
                    last_agent_status = excluded.last_agent_status,
                    last_agent_error = excluded.last_agent_error,
                    last_agent_sha = excluded.last_agent_sha
                """,
                (
                    project_id,
                    team,
                    sub_status.value,
                    summary,
                    onboarded_at.isoformat() if onboarded_at else None,
                    now.isoformat(),
                    status,
                    error,
                    commit_sha,
                ),
            )
            await self._rebuild_and_save(db, project)
            await db.commit()
            members = await self._load_subprojects(db, project_id)
            return next(item for item in members if item.team == team)

    async def _ensure_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subprojects (
                project_id TEXT NOT NULL,
                team TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                onboarded_at TEXT,
                PRIMARY KEY (project_id, team),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS changes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                team TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                details TEXT NOT NULL,
                UNIQUE (project_id, version),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aggregated_state (
                project_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS hub_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                poll_interval_seconds INTEGER NOT NULL DEFAULT 30,
                auto_sync_enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )

    async def _ensure_auth_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                hub_role TEXT NOT NULL DEFAULT 'member',
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS project_members (
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (project_id, user_id),
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix)")

    async def _ensure_subproject_agent_columns(self, db: aiosqlite.Connection) -> None:
        columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(subprojects)")}
        additions = {
            "last_agent_at": "TEXT",
            "last_agent_status": "TEXT NOT NULL DEFAULT ''",
            "last_agent_error": "TEXT NOT NULL DEFAULT ''",
            "last_agent_sha": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in columns:
                await db.execute(f"ALTER TABLE subprojects ADD COLUMN {name} {ddl}")

    async def _ensure_hub_settings(self, db: aiosqlite.Connection) -> None:
        rows = await db.execute_fetchall("SELECT 1 FROM hub_settings WHERE id = 1")
        if not rows:
            await db.execute(
                "INSERT INTO hub_settings (id, poll_interval_seconds, auto_sync_enabled) VALUES (1, 30, 1)"
            )

    async def _ensure_project_columns(self, db: aiosqlite.Connection) -> None:
        columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(projects)")}
        additions = {
            "openapi_url": "TEXT NOT NULL DEFAULT ''",
            "auto_sync": "INTEGER NOT NULL DEFAULT 1",
            "sync_mode": "TEXT NOT NULL DEFAULT 'interval'",
            "git_repo_path": "TEXT NOT NULL DEFAULT ''",
            "last_git_sha": "TEXT NOT NULL DEFAULT ''",
            "last_sync_at": "TEXT",
            "last_sync_status": "TEXT NOT NULL DEFAULT ''",
            "last_sync_error": "TEXT NOT NULL DEFAULT ''",
            "openapi_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "teams": "TEXT NOT NULL DEFAULT '[\"frontend\",\"backend\",\"other\"]'",
        }
        for name, ddl in additions.items():
            if name not in columns:
                await db.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")

    async def _migrate_legacy(self, db: aiosqlite.Connection) -> None:
        columns = await db.execute_fetchall("PRAGMA table_info(changes)")
        column_names = {row[1] for row in columns}
        if not columns or "project_id" in column_names:
            return

        # Legacy single-project schema without project_id.
        legacy_changes = await db.execute_fetchall(
            "SELECT id, version, timestamp, team, type, description, details FROM changes ORDER BY version ASC"
        )
        legacy_state_rows = await db.execute_fetchall("SELECT state FROM aggregated_state WHERE id = 1")

        await db.execute("ALTER TABLE changes RENAME TO changes_legacy")
        await db.execute("ALTER TABLE aggregated_state RENAME TO aggregated_state_legacy")
        await self._ensure_schema(db)

        if not legacy_changes and not legacy_state_rows:
            return

        project_id = slugify(self.default_project_name)
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """
            INSERT OR IGNORE INTO projects (id, name, description, created_at, updated_at)
            VALUES (?, ?, '', ?, ?)
            """,
            (project_id, self.default_project_name, now, now),
        )
        for row in legacy_changes:
            await db.execute(
                """
                INSERT INTO changes (id, project_id, version, timestamp, team, type, description, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row[0], project_id, row[1], row[2], row[3], row[4], row[5], row[6]),
            )
        if legacy_changes:
            changes = [
                Change(
                    id=row[0],
                    project_id=project_id,
                    version=row[1],
                    timestamp=datetime.fromisoformat(row[2]),
                    team=row[3],
                    type=row[4],
                    description=row[5],
                    details=json.loads(row[6] or "{}"),
                )
                for row in legacy_changes
            ]
            state = rebuild_state(self.default_project_name, changes, project_id=project_id)
        elif legacy_state_rows:
            state = ProjectState.model_validate_json(legacy_state_rows[0][0])
            state.project_id = project_id
            state.project = self.default_project_name
        else:
            state = empty_state(self.default_project_name, project_id)
        await db.execute(
            "INSERT INTO aggregated_state (project_id, state) VALUES (?, ?)",
            (project_id, state.model_dump_json()),
        )
        await db.execute("DROP TABLE IF EXISTS changes_legacy")
        await db.execute("DROP TABLE IF EXISTS aggregated_state_legacy")

    async def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        suffix = 2
        async with aiosqlite.connect(self.path) as db:
            while True:
                rows = await db.execute_fetchall("SELECT 1 FROM projects WHERE id = ?", (candidate,))
                if not rows:
                    return candidate
                candidate = f"{base}-{suffix}"
                suffix += 1

    async def _require_project(self, db: aiosqlite.Connection, project_id: str) -> Project:
        rows = await db.execute_fetchall("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not rows:
            raise ProjectNotFoundError(project_id)
        row = rows[0]
        if isinstance(row, aiosqlite.Row):
            return self._project_from_row(row)
        # Fallback tuple order when row_factory is unset.
        return Project(
            id=row[0],
            name=row[1],
            description=row[2] or "",
            created_at=datetime.fromisoformat(row[3]),
            updated_at=datetime.fromisoformat(row[4]),
        )

    def _user_from_row(self, row: aiosqlite.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            hub_role=HubRole(row["hub_role"]),
            disabled=bool(row["disabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _user_public_from_row(self, row: aiosqlite.Row) -> UserPublic:
        user = self._user_from_row(row)
        return UserPublic(
            id=user.id,
            username=user.username,
            hub_role=user.hub_role,
            disabled=user.disabled,
            created_at=user.created_at,
        )

    def _api_key_from_row(self, row: aiosqlite.Row) -> ApiKeyRecord:
        return ApiKeyRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            prefix=row["prefix"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used_at=datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
            revoked_at=datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def _project_from_row(self, row: aiosqlite.Row) -> Project:
        keys = set(row.keys())
        mode_raw = (row["sync_mode"] if "sync_mode" in keys else "interval") or "interval"
        try:
            sync_mode = SyncMode(mode_raw)
        except ValueError:
            sync_mode = SyncMode.interval
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            openapi_url=(row["openapi_url"] if "openapi_url" in keys else "") or "",
            auto_sync=bool(row["auto_sync"]) if "auto_sync" in keys else True,
            sync_mode=sync_mode,
            git_repo_path=(row["git_repo_path"] if "git_repo_path" in keys else "") or "",
            last_git_sha=(row["last_git_sha"] if "last_git_sha" in keys else "") or "",
            last_sync_at=(
                datetime.fromisoformat(row["last_sync_at"])
                if "last_sync_at" in keys and row["last_sync_at"]
                else None
            ),
            last_sync_status=(row["last_sync_status"] if "last_sync_status" in keys else "") or "",
            last_sync_error=(row["last_sync_error"] if "last_sync_error" in keys else "") or "",
            openapi_fingerprint=(row["openapi_fingerprint"] if "openapi_fingerprint" in keys else "") or "",
            teams=(
                json.loads(row["teams"])
                if "teams" in keys and row["teams"]
                else list(DEFAULT_PROJECT_TEAMS)
            ),
        )

    def _summary_from_row(self, row: aiosqlite.Row, state: ProjectState) -> ProjectSummary:
        project = self._project_from_row(row)
        return ProjectSummary(
            id=project.id,
            name=project.name,
            description=project.description,
            version=state.version,
            updated_at=project.updated_at,
            open_requirements=sum(1 for item in state.requirements if item.status == "open"),
            api_count=len(state.api),
            component_count=len(state.components),
            artifact_count=len(state.artifacts),
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
            teams=list(project.teams),
        )

    async def _next_version(self, db: aiosqlite.Connection, project_id: str) -> int:
        row = await db.execute_fetchall(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM changes WHERE project_id = ?",
            (project_id,),
        )
        return int(row[0][0])

    async def _insert_change(self, db: aiosqlite.Connection, change: Change) -> None:
        await db.execute(
            """
            INSERT INTO changes (id, project_id, version, timestamp, team, type, description, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change.id,
                change.project_id,
                change.version,
                change.timestamp.isoformat(),
                change.team,
                change.type.value,
                change.description,
                json.dumps(change.details),
            ),
        )

    async def _load_changes(self, db: aiosqlite.Connection, project_id: str) -> list[Change]:
        rows = await db.execute_fetchall(
            "SELECT * FROM changes WHERE project_id = ? ORDER BY version ASC",
            (project_id,),
        )
        return [
            Change(
                id=row["id"],
                project_id=row["project_id"],
                version=row["version"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                team=row["team"],
                type=row["type"],
                description=row["description"],
                details=json.loads(row["details"] or "{}"),
            )
            for row in rows
        ]

    async def _load_subprojects(self, db: aiosqlite.Connection, project_id: str) -> list[SubprojectRecord]:
        rows = await db.execute_fetchall(
            "SELECT * FROM subprojects WHERE project_id = ? ORDER BY team ASC",
            (project_id,),
        )
        return [
            SubprojectRecord(
                team=row["team"],
                status=row["status"],
                summary=row["summary"] or "",
                onboarded_at=datetime.fromisoformat(row["onboarded_at"]) if row["onboarded_at"] else None,
                last_agent_at=(
                    datetime.fromisoformat(row["last_agent_at"])
                    if "last_agent_at" in row.keys() and row["last_agent_at"]
                    else None
                ),
                last_agent_status=(row["last_agent_status"] if "last_agent_status" in row.keys() else "") or "",
                last_agent_error=(row["last_agent_error"] if "last_agent_error" in row.keys() else "") or "",
                last_agent_sha=(row["last_agent_sha"] if "last_agent_sha" in row.keys() else "") or "",
            )
            for row in rows
        ]

    async def _upsert_subproject(
        self,
        db: aiosqlite.Connection,
        project_id: str,
        team: Team,
        status: SubprojectStatus,
        summary: str = "",
    ) -> SubprojectRecord:
        onboarded_at = datetime.now(UTC) if status == SubprojectStatus.ready else None
        await db.execute(
            """
            INSERT INTO subprojects (project_id, team, status, summary, onboarded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, team) DO UPDATE SET
                status = excluded.status,
                summary = excluded.summary,
                onboarded_at = excluded.onboarded_at
            """,
            (
                project_id,
                team,
                status.value,
                summary,
                onboarded_at.isoformat() if onboarded_at else None,
            ),
        )
        return SubprojectRecord(team=team, status=status, summary=summary, onboarded_at=onboarded_at)

    async def _load_state(self, db: aiosqlite.Connection, project_id: str, project_name: str) -> ProjectState:
        rows = await db.execute_fetchall(
            "SELECT state FROM aggregated_state WHERE project_id = ?",
            (project_id,),
        )
        subprojects = await self._load_subprojects(db, project_id)
        if not rows:
            return empty_state(project_name, project_id, subprojects)
        state = ProjectState.model_validate_json(rows[0][0] if not isinstance(rows[0], aiosqlite.Row) else rows[0]["state"])
        state.project_id = project_id
        state.project = project_name
        state.subprojects = subprojects
        return state

    async def _rebuild_and_save(self, db: aiosqlite.Connection, project: Project) -> ProjectState:
        changes = await self._load_changes(db, project.id)
        subprojects = await self._load_subprojects(db, project.id)
        state = rebuild_state(project.name, changes, project_id=project.id, subprojects=subprojects)
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """
            INSERT INTO aggregated_state (project_id, state)
            VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET state = excluded.state
            """,
            (project.id, state.model_dump_json()),
        )
        await db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project.id))
        return state


def filter_changes(
    changes: list[Change],
    *,
    since: str | None,
    team: str | None,
    change_type: str | None,
) -> list[Change]:
    selected = changes
    if since:
        if since.isdigit():
            version = int(since)
            selected = [change for change in selected if change.version > version]
        else:
            timestamp = datetime.fromisoformat(since.replace("Z", "+00:00"))
            selected = [change for change in selected if change.timestamp >= timestamp]
    if team:
        selected = [change for change in selected if change.team == team]
    if change_type:
        selected = [change for change in selected if change.type.value == change_type]
    return selected


# Backwards-compatible alias used by json_store / tests
_filter_changes = filter_changes
