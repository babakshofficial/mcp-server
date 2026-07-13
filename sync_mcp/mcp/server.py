from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from sync_mcp.config import Settings
from sync_mcp.models import ChangeCreate, PublishResult
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.state import format_state_markdown
from sync_mcp.storage.base import StateStore


def create_mcp_server(store: StateStore, notifier: ChangeNotifier, settings: Settings) -> FastMCP:
    mcp = FastMCP("team-sync", json_response=True, stateless_http=True)

    @mcp.tool()
    async def publish_update(
        team: str,
        type: str,
        description: str,
        details: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Publish a frontend/backend change into the shared project state."""
        saved, next_state = await store.publish(
            ChangeCreate(team=team, type=type, description=description, details=details or {})
        )
        await notifier.publish(saved, next_state)
        if ctx is not None:
            await _notify_mcp_resources(ctx)
        return PublishResult(change_id=saved.id, version=saved.version, state=next_state).model_dump(mode="json")

    @mcp.tool()
    async def get_latest_state() -> dict[str, Any]:
        """Return the full aggregated state plus a Cursor-friendly digest."""
        state = await store.get_state()
        payload = state.model_dump(mode="json")
        payload["digest_markdown"] = format_state_markdown(state)
        return payload

    @mcp.tool()
    async def get_changelog(
        since: str | None = None,
        team: str | None = None,
        type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return changes since an ISO timestamp or version, optionally filtered by team/type."""
        changes = await store.get_changelog(since=since, team=team, change_type=type, limit=limit)
        return [change.model_dump(mode="json") for change in changes]

    @mcp.tool()
    async def subscribe_to_changes() -> dict[str, str]:
        """Describe subscription resources clients can watch for near-real-time updates."""
        return {
            "state_resource": "sync://state",
            "changelog_resource": "sync://changelog",
            "dashboard_events": "/api/events",
            "note": "Subscribe to sync://state when your MCP client supports resource update notifications.",
        }

    @mcp.resource("sync://state")
    async def state_resource() -> str:
        """Current aggregated project state."""
        return (await store.get_state()).model_dump_json(indent=2)

    @mcp.resource("sync://changelog")
    async def changelog_resource() -> str:
        """Recent project changelog entries."""
        changes = await store.get_changelog(limit=50)
        return "[" + ",".join(change.model_dump_json(indent=2) for change in changes) + "]"

    @mcp.prompt()
    async def sync_digest(team: str | None = None) -> str:
        """Create a markdown digest of the latest shared state for Cursor chat."""
        state = await store.get_state()
        changes = await store.get_changelog(team=team, limit=10)
        lines = [format_state_markdown(state), "", "## Recent changes"]
        lines.extend(f"- [{change.team}] {change.type}: {change.description}" for change in changes)
        return "\n".join(lines)

    return mcp


async def _notify_mcp_resources(ctx: Context) -> None:
    try:
        await ctx.session.send_resource_updated("sync://state")
        await ctx.session.send_resource_updated("sync://changelog")
    except Exception:
        await ctx.info("Published update; resource notification was not supported by this client.")
