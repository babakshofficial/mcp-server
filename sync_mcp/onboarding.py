from __future__ import annotations

from sync_mcp.models import Team, Teams


def onboard_checklist(team: Team) -> list[str]:
    if team == Teams.backend:
        return [
            "Prefer FastAPI OpenAPI: fetch `{API_BASE}/openapi.json` (or read a checked-in openapi.json) and import it.",
            "If OpenAPI is unavailable, list public HTTP routes (method + path) from the framework of record.",
            "Capture request/response shapes, auth requirements, and important error codes for each route.",
            "Note shared domain models / DTOs other teams consume.",
            "Record breaking or pending contract changes as open requirements if they are not finished.",
            "Summarize env/config other teams must know (base URL patterns, API versioning).",
        ]
    if team == Teams.frontend:
        return [
            "Inventory shared UI components that encode product contracts (forms, tables, auth shells).",
            "List API clients / fetch wrappers and which backend endpoints they call.",
            "Document route pages and the data they expect from the backend.",
            "Capture known backend gaps as open requirements.",
            "Note design-system or prop-level contracts other teams should respect.",
        ]
    return [
        "Describe this subproject's responsibility and artifacts other teams consume.",
        "Publish any APIs, schemas, jobs, or UI surfaces relevant to FE/BE coordination.",
        "List open cross-team requirements.",
    ]


def onboard_instructions(project_id: str, team: Team) -> str:
    checklist = "\n".join(f"{index}. {item}" for index, item in enumerate(onboard_checklist(team), start=1))
    openapi_hint = ""
    if team == Teams.backend:
        openapi_hint = (
            "\nFor FastAPI backends, prefer calling `import_openapi` with the running API's "
            "`http://localhost:<port>/openapi.json` (or paste the OpenAPI JSON). "
            "Only fall back to manual route scanning if OpenAPI is unavailable.\n"
        )
    agent_hint = (
        "\nFor continuous autonomous updates on this machine, run `python -m sync_agent` "
        f"with SYNC_AGENT_PROJECT=`{project_id}-{team}`, mode `on_commit` or `schedule` "
        "(see README: Team-local sync agents).\n"
    )
    return (
        f"You are onboarding the `{team}` subproject into Team Sync project `{project_id}`.\n"
        "Explore the currently open workspace thoroughly, then publish findings.\n"
        f"{openapi_hint}"
        f"{agent_hint}\n"
        "Checklist:\n"
        f"{checklist}\n\n"
        "When finished, call `import_snapshot` (or `import_openapi` for FastAPI) with:\n"
        f"- project_id: `{project_id}`\n"
        f"- team: `{team}`\n"
        "- api: discovered endpoints (backend-heavy)\n"
        "- components: discovered UI/component contracts (frontend-heavy)\n"
        "- requirements: open cross-team needs\n"
        "- notes: short summary of what you scanned\n"
    )
