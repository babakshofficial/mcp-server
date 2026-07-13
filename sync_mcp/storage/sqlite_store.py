from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from sync_mcp.models import Change, ChangeCreate, ProjectState
from sync_mcp.state import empty_state, rebuild_state
from sync_mcp.storage.base import StateStore


class SQLiteStateStore(StateStore):
    def __init__(self, path: Path, project: str) -> None:
        self.path = path
        self.project = project

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS changes (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    team TEXT NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    details TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS aggregated_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL
                )
                """
            )
            row = await db.execute_fetchall("SELECT state FROM aggregated_state WHERE id = 1")
            if not row:
                await db.execute(
                    "INSERT INTO aggregated_state (id, state) VALUES (1, ?)",
                    (empty_state(self.project).model_dump_json(),),
                )
            await db.commit()

    async def publish(self, change: ChangeCreate) -> tuple[Change, ProjectState]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            version = await self._next_version(db)
            saved = Change(
                version=version,
                team=change.team,
                type=change.type,
                description=change.description,
                details=change.details,
            )
            await db.execute(
                """
                INSERT INTO changes (id, version, timestamp, team, type, description, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved.id,
                    saved.version,
                    saved.timestamp.isoformat(),
                    saved.team.value,
                    saved.type.value,
                    saved.description,
                    json.dumps(saved.details),
                ),
            )
            changes = await self._load_changes(db)
            state = rebuild_state(self.project, changes)
            await db.execute(
                """
                INSERT INTO aggregated_state (id, state)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET state = excluded.state
                """,
                (state.model_dump_json(),),
            )
            await db.commit()
            return saved, state

    async def get_state(self) -> ProjectState:
        async with aiosqlite.connect(self.path) as db:
            row = await db.execute_fetchall("SELECT state FROM aggregated_state WHERE id = 1")
            if not row:
                return empty_state(self.project)
            return ProjectState.model_validate_json(row[0][0])

    async def get_changelog(
        self,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            changes = await self._load_changes(db)

        filtered = _filter_changes(changes, since=since, team=team, change_type=change_type)
        return sorted(filtered, key=lambda item: item.version, reverse=True)[:limit]

    async def _next_version(self, db: aiosqlite.Connection) -> int:
        row = await db.execute_fetchall("SELECT COALESCE(MAX(version), 0) + 1 FROM changes")
        return int(row[0][0])

    async def _load_changes(self, db: aiosqlite.Connection) -> list[Change]:
        rows = await db.execute_fetchall("SELECT * FROM changes ORDER BY version ASC")
        return [
            Change(
                id=row["id"],
                version=row["version"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                team=row["team"],
                type=row["type"],
                description=row["description"],
                details=json.loads(row["details"] or "{}"),
            )
            for row in rows
        ]


def _filter_changes(
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
