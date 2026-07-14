from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

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


class ProjectNotFoundError(LookupError):
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
    ) -> Project:
        project_id = await self._unique_slug(name)
        now = datetime.now(UTC)
        mode = SyncMode(sync_mode)
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
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO projects (
                    id, name, description, created_at, updated_at,
                    openapi_url, auto_sync, sync_mode, git_repo_path, last_git_sha,
                    last_sync_at, last_sync_status, last_sync_error, openapi_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, '', '', '')
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
                ),
            )
            await db.execute(
                "INSERT INTO aggregated_state (project_id, state) VALUES (?, ?)",
                (project.id, empty_state(project.name, project.id).model_dump_json()),
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
                    openapi_fingerprint = ?, updated_at = ?
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
                    now,
                    project_id,
                ),
            )
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM projects WHERE id = ?", (project_id,))
            return self._project_from_row(rows[0])

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
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            project = await self._require_project(db, project_id)
            synthetic: list[ChangeCreate] = []
            for endpoint in snapshot.api:
                synthetic.append(
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
                synthetic.append(
                    ChangeCreate(
                        team=snapshot.team,
                        type=ChangeType.component_spec,
                        description=component.name,
                        details={"name": component.name, "spec": component.spec, **component.details},
                    )
                )
            for requirement in snapshot.requirements:
                synthetic.append(
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
            ]
            notes = snapshot.notes.strip() or "Initial codebase snapshot"
            digest_version = await self._next_version(db, project_id)
            digest = Change(
                project_id=project_id,
                version=digest_version,
                team=snapshot.team,
                type=ChangeType.changelog,
                description=f"{snapshot.team.value} onboarded: {', '.join(summary_bits)}. {notes}",
                details={"source": "import_snapshot", "notes": notes},
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
                change.team.value,
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
                team.value,
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
        selected = [change for change in selected if change.team.value == team]
    if change_type:
        selected = [change for change in selected if change.type.value == change_type]
    return selected


# Backwards-compatible alias used by json_store / tests
_filter_changes = filter_changes
