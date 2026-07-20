from __future__ import annotations

from typing import Any

from sync_mcp.models import ApiEndpoint, Team, Teams


def endpoints_from_openapi(spec: dict[str, Any], *, team: Team = Teams.backend) -> list[ApiEndpoint]:
    """Convert an OpenAPI 3.x / Swagger 2 document into Team Sync API endpoints."""
    paths = spec.get("paths") or {}
    endpoints: list[ApiEndpoint] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() in {"parameters", "summary", "description", "servers"}:
                continue
            if not isinstance(operation, dict):
                continue
            summary = str(operation.get("summary") or operation.get("operationId") or "")
            description = str(operation.get("description") or summary)
            tags = operation.get("tags") or []
            endpoints.append(
                ApiEndpoint(
                    method=method.upper(),
                    path=str(path),
                    description=description or summary or f"{method.upper()} {path}",
                    team=team,
                    details={
                        "source": "openapi",
                        "operationId": operation.get("operationId"),
                        "tags": tags,
                        "parameters": operation.get("parameters"),
                        "requestBody": operation.get("requestBody"),
                        "responses": _response_keys(operation.get("responses")),
                    },
                )
            )
    endpoints.sort(key=lambda item: (item.path, item.method))
    return endpoints


def _response_keys(responses: Any) -> list[str]:
    if not isinstance(responses, dict):
        return []
    return [str(key) for key in responses.keys()]
