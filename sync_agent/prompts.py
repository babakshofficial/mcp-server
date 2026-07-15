from __future__ import annotations


def crawl_prompt(*, project_header: str, project_id: str, team: str, openapi_url: str = "") -> str:
    """Build the one-shot crawl + MCP update prompt for a subproject team."""
    shared = (
        f"You are an autonomous Team Sync agent for project header `{project_header}` "
        f"(project_id `{project_id}`, team `{team}`).\n"
        "Use the Team Sync MCP tools (farzan-mcp / team-sync) already configured.\n"
        "project_id and team may be omitted on tools because the Project header scopes them.\n"
        "Explore THIS workspace thoroughly. Do not invent endpoints or components that are not in the code.\n"
    )

    if team == "backend":
        openapi_line = (
            f"- Prefer `import_openapi` with openapi_url=`{openapi_url}`.\n"
            if openapi_url
            else (
                "- Prefer `import_openapi` if you find a reachable FastAPI `/openapi.json` "
                "(try common local URLs from README/.env, or a checked-in openapi.json).\n"
            )
        )
        return (
            shared
            + "Backend goals:\n"
            + openapi_line
            + "- If OpenAPI is unavailable, scan routes and call `import_snapshot` with api endpoints.\n"
            + "- Include breaking/pending contract notes as requirements when relevant.\n"
            + "- Call `get_latest_state` after publishing and briefly summarize what changed.\n"
            + "Finish only after MCP tools have been called successfully.\n"
        )

    if team == "frontend":
        return (
            shared
            + "Frontend goals:\n"
            + "- First call `get_latest_state` to see current shared API/contracts.\n"
            + "- Inventory UI components, API clients, routes, and cross-team requirements.\n"
            + "- Call `import_snapshot` with components and requirements (and api only if FE owns them).\n"
            + "- Call `get_latest_state` again and summarize.\n"
            + "Finish only after MCP tools have been called successfully.\n"
        )

    return (
        shared
        + "Goals:\n"
        + "- Describe artifacts other teams consume; publish via `import_snapshot`.\n"
        + "- Call `get_latest_state` after publishing and summarize.\n"
    )
