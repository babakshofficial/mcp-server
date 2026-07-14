from __future__ import annotations

import hashlib
from typing import Any

from sync_mcp.models import ApiEndpoint, ChangeCreate, ChangeType, Team
from sync_mcp.openapi_import import endpoints_from_openapi


def openapi_fingerprint(endpoints: list[ApiEndpoint]) -> str:
    lines = [
        f"{item.method}|{item.path}|{item.description}|{item.details.get('operationId', '')}"
        for item in sorted(endpoints, key=lambda e: (e.path, e.method))
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def diff_openapi_changes(
    current: list[ApiEndpoint],
    discovered: list[ApiEndpoint],
    *,
    team: Team = Team.backend,
) -> list[ChangeCreate]:
    """Diff current API surface against OpenAPI discoveries."""
    current_map = {(item.method, item.path): item for item in current}
    discovered_map = {(item.method, item.path): item for item in discovered}
    changes: list[ChangeCreate] = []

    for key, endpoint in discovered_map.items():
        existing = current_map.get(key)
        details = {
            "method": endpoint.method,
            "path": endpoint.path,
            "description": endpoint.description,
            **endpoint.details,
        }
        if existing is None:
            changes.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.api_added,
                    description=f"{endpoint.method} {endpoint.path}",
                    details=details,
                )
            )
        elif _endpoint_signature(existing) != _endpoint_signature(endpoint):
            changes.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.api_changed,
                    description=f"{endpoint.method} {endpoint.path}",
                    details=details,
                )
            )

    for key, endpoint in current_map.items():
        # Only auto-remove endpoints previously imported from OpenAPI.
        if key not in discovered_map and endpoint.details.get("source") == "openapi":
            changes.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.api_removed,
                    description=f"{endpoint.method} {endpoint.path}",
                    details={"method": endpoint.method, "path": endpoint.path},
                )
            )
    return changes


def endpoints_and_fingerprint(spec: dict[str, Any], *, team: Team = Team.backend) -> tuple[list[ApiEndpoint], str]:
    endpoints = endpoints_from_openapi(spec, team=team)
    return endpoints, openapi_fingerprint(endpoints)


def _endpoint_signature(endpoint: ApiEndpoint) -> str:
    return f"{endpoint.method}|{endpoint.path}|{endpoint.description}|{endpoint.details.get('operationId', '')}"
