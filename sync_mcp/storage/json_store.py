from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sync_mcp.models import Change, ChangeCreate, ProjectState
from sync_mcp.state import empty_state, rebuild_state
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.sqlite_store import _filter_changes


class JSONStateStore(StateStore):
    def __init__(self, path: Path, project: str) -> None:
        self.path = path
        self.project = project
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            await self._write({"changes": [], "state": empty_state(self.project).model_dump(mode="json")})

    async def publish(self, change: ChangeCreate) -> tuple[Change, ProjectState]:
        async with self._lock:
            payload = await self._read()
            changes = [Change.model_validate(item) for item in payload.get("changes", [])]
            saved = Change(
                version=(max((item.version for item in changes), default=0) + 1),
                team=change.team,
                type=change.type,
                description=change.description,
                details=change.details,
            )
            changes.append(saved)
            state = rebuild_state(self.project, changes)
            await self._write(
                {
                    "changes": [item.model_dump(mode="json") for item in changes],
                    "state": state.model_dump(mode="json"),
                }
            )
            return saved, state

    async def get_state(self) -> ProjectState:
        payload = await self._read()
        state = payload.get("state")
        return ProjectState.model_validate(state) if state else empty_state(self.project)

    async def get_changelog(
        self,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        payload = await self._read()
        changes = [Change.model_validate(item) for item in payload.get("changes", [])]
        filtered = _filter_changes(changes, since=since, team=team, change_type=change_type)
        return sorted(filtered, key=lambda item: item.version, reverse=True)[:limit]

    async def _read(self) -> dict:
        return await asyncio.to_thread(lambda: json.loads(self.path.read_text() or "{}"))

    async def _write(self, payload: dict) -> None:
        await asyncio.to_thread(lambda: self.path.write_text(json.dumps(payload, indent=2)))
