from __future__ import annotations

from sync_mcp.config import Settings
from sync_mcp.storage.base import StateStore
from sync_mcp.storage.json_store import JSONStateStore
from sync_mcp.storage.sqlite_store import SQLiteStateStore


def create_store(settings: Settings) -> StateStore:
    if settings.storage == "json":
        return JSONStateStore(settings.json_path, settings.project)
    return SQLiteStateStore(settings.sqlite_path, settings.project)
