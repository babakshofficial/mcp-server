from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from sync_agent.config import AgentSettings
from sync_mcp.http_proxy import sync_client_for

logger = logging.getLogger(__name__)


def report_agent_status(
    settings: AgentSettings,
    *,
    status: str,
    error: str = "",
    commit_sha: str = "",
) -> None:
    """Best-effort REST report so the hub dashboard can show last agent run."""
    project_id, team = settings.project_name_and_team()
    url = urljoin(settings.resolve_rest_base() + "/", f"api/projects/{project_id}/agent-status")
    try:
        with sync_client_for(url, timeout=20.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json={"team": team, "status": status, "error": error, "commit_sha": commit_sha},
            )
        if response.status_code == 404:
            logger.warning(
                "Agent status report failed: 404 (hub at %s is likely outdated — "
                "rebuild/redeploy so POST /api/projects/{id}/agent-status exists). %s",
                settings.resolve_rest_base(),
                response.text[:200],
            )
        elif response.status_code >= 400:
            logger.warning("Agent status report failed: %s %s", response.status_code, response.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent status report error: %s", exc)


def build_mcp_servers(settings: AgentSettings) -> dict[str, Any]:
    """Build HttpMcpServerConfig mapping; uses dict form for easier mocking in tests."""
    name, team = settings.project_name_and_team()
    return {
        "team-sync": {
            "type": "http",
            "url": settings.hub_url,
            "headers": {
                "Authorization": f"Bearer {settings.api_key}",
                "Project": name,
                "Team": team,
            },
        }
    }
