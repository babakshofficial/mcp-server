from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from sync_agent.config import AgentSettings

logger = logging.getLogger(__name__)


def report_agent_status(
    settings: AgentSettings,
    *,
    status: str,
    error: str = "",
    commit_sha: str = "",
) -> None:
    """Best-effort REST report so the hub dashboard can show last agent run."""
    _, team = settings.project_name_and_team()
    project_id = _project_id_from_header(settings.project)
    url = urljoin(settings.resolve_rest_base() + "/", f"api/projects/{project_id}/agent-status")
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json={"team": team, "status": status, "error": error, "commit_sha": commit_sha},
            timeout=20.0,
        )
        if response.status_code >= 400:
            logger.warning("Agent status report failed: %s %s", response.status_code, response.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent status report error: %s", exc)


def _project_id_from_header(project_header: str) -> str:
    # Header is name-team; hub slug is usually the name portion (may differ if slugified).
    name, _ = project_header.rsplit("-", 1)
    return name.strip().lower().replace(" ", "-")


def build_mcp_servers(settings: AgentSettings) -> dict[str, Any]:
    """Build HttpMcpServerConfig mapping; uses dict form for easier mocking in tests."""
    return {
        "team-sync": {
            "type": "http",
            "url": settings.hub_url,
            "headers": {
                "Authorization": f"Bearer {settings.api_key}",
                "Project": settings.project,
            },
        }
    }
