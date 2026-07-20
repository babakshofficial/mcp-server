from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from sync_mcp.git_watch import GitWatchError, read_head_sha
from sync_mcp.http_proxy import async_client_for
from sync_mcp.models import ChangeCreate, ChangeType, HubSettings, Project, SubprojectStatus, SyncMode, Team, Teams
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.openapi_diff import diff_openapi_changes, endpoints_and_fingerprint
from sync_mcp.storage.base import StateStore

logger = logging.getLogger(__name__)

_UNREACHABLE_OPENAPI_HOSTS = frozenset({"0.0.0.0", "::", "[::]"})


def _openapi_url_unreachable_reason(openapi_url: str) -> str | None:
    host = (urlparse(openapi_url).hostname or "").lower()
    if host in _UNREACHABLE_OPENAPI_HOSTS:
        return (
            f"{openapi_url!r} uses bind address {host!r}, which is not a fetchable host. "
            "Set OpenAPI URL to the host LAN IP, e.g. http://192.168.17.29:8001/openapi.json"
        )
    return None


@dataclass
class SyncCycleResult:
    checked: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0


class AutoSyncService:
    def __init__(self, store: StateStore, notifier: ChangeNotifier) -> None:
        self.store = store
        self.notifier = notifier
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="auto-sync-poller")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def notify_settings_changed(self) -> None:
        self._wake.set()

    async def sync_project(self, project: Project, *, trigger: str = "auto_sync", commit_sha: str | None = None) -> bool:
        """Fetch OpenAPI and apply diffs. Returns True if state changed."""
        if not project.openapi_url:
            return False
        bad = _openapi_url_unreachable_reason(project.openapi_url)
        if bad:
            logger.warning("Auto-sync failed for %s: %s", project.id, bad)
            await self.store.update_sync_status(project.id, status="error", error=bad)
            return False
        try:
            async with async_client_for(project.openapi_url, timeout=20.0) as client:
                response = await client.get(project.openapi_url)
                response.raise_for_status()
                spec = response.json()
            endpoints, fingerprint = endpoints_and_fingerprint(spec, team=Teams.backend)
            sha_kw: dict[str, str] = {}
            if commit_sha is not None:
                sha_kw["last_git_sha"] = commit_sha

            if fingerprint == project.openapi_fingerprint:
                await self.store.update_sync_status(
                    project.id,
                    status="ok",
                    error="",
                    fingerprint=fingerprint,
                    **sha_kw,
                )
                return False

            state = await self.store.get_state(project.id)
            changes = diff_openapi_changes(state.api, endpoints, team=Teams.backend)
            if not changes:
                await self.store.update_sync_status(
                    project.id,
                    status="ok",
                    error="",
                    fingerprint=fingerprint,
                    **sha_kw,
                )
                return False

            last_change = None
            next_state = state
            for change in changes:
                last_change, next_state = await self.store.publish(project.id, change)

            summary = ChangeCreate(
                team=Teams.backend,
                type=ChangeType.changelog,
                description=(
                    f"Auto-sync from OpenAPI: {len(changes)} change(s) "
                    f"({project.openapi_url})"
                ),
                details={
                    "source": trigger,
                    "openapi_url": project.openapi_url,
                    "changed": len(changes),
                    "endpoint_count": len(endpoints),
                    **({"commit_sha": commit_sha} if commit_sha else {}),
                },
            )
            last_change, next_state = await self.store.publish(project.id, summary)
            await self.store.mark_subproject(
                project.id,
                Teams.backend,
                SubprojectStatus.ready,
                summary="Auto-synced from OpenAPI",
            )
            await self.store.update_sync_status(
                project.id,
                status="updated",
                error="",
                fingerprint=fingerprint,
                **sha_kw,
            )
            if last_change is not None:
                await self.notifier.publish(last_change, next_state)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Auto-sync failed for %s (%s): %s",
                project.id,
                project.openapi_url,
                exc,
            )
            await self.store.update_sync_status(project.id, status="error", error=str(exc))
            return False

    async def maybe_sync_project(self, project: Project) -> str:
        """
        Run one project's sync according to sync_mode.

        Returns: 'updated' | 'unchanged' | 'skipped' | 'failed'
        """
        if project.sync_mode == SyncMode.on_commit:
            if not project.git_repo_path:
                await self.store.update_sync_status(
                    project.id,
                    status="error",
                    error="on_commit sync requires git_repo_path",
                )
                return "failed"
            try:
                sha = await read_head_sha(project.git_repo_path)
            except GitWatchError as exc:
                logger.warning("Git watch failed for %s: %s", project.id, exc)
                await self.store.update_sync_status(project.id, status="error", error=str(exc))
                return "failed"
            if sha == project.last_git_sha:
                return "skipped"
            changed = await self.sync_project(project, trigger="on_commit", commit_sha=sha)
            return "updated" if changed else "unchanged"

        changed = await self.sync_project(project, trigger="interval")
        return "updated" if changed else "unchanged"

    async def run_once(self) -> SyncCycleResult:
        result = SyncCycleResult()
        settings = await self.store.get_hub_settings()
        if not settings.auto_sync_enabled:
            return result
        projects = await self.store.list_auto_sync_targets()
        for project in projects:
            result.checked += 1
            try:
                outcome = await self.maybe_sync_project(project)
                if outcome == "updated":
                    result.updated += 1
                elif outcome == "skipped":
                    result.skipped += 1
                elif outcome == "failed":
                    result.failed += 1
                else:
                    result.unchanged += 1
            except Exception:  # noqa: BLE001
                result.failed += 1
        return result

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("Auto-sync cycle failed")
            settings = await self.store.get_hub_settings()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(5, settings.poll_interval_seconds))
                self._wake.clear()
            except TimeoutError:
                pass
