from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Callable, TypeVar

from sync_mcp.models import ApiEndpoint, Change, ChangeType, ComponentSpec, ProjectState, Requirement, SubprojectRecord

T = TypeVar("T")


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:80] or "project"


def empty_state(project: str, project_id: str = "", subprojects: list[SubprojectRecord] | None = None) -> ProjectState:
    return ProjectState(
        project=project,
        project_id=project_id or slugify(project),
        subprojects=list(subprojects or []),
    )


def apply_change(state: ProjectState, change: Change) -> ProjectState:
    data = change.details
    state.version = max(state.version, change.version)
    state.updated_at = change.timestamp
    if change.project_id:
        state.project_id = change.project_id

    if change.type in {ChangeType.api_added, ChangeType.api_changed}:
        endpoint = ApiEndpoint(
            method=str(data.get("method", "GET")).upper(),
            path=str(data.get("path") or data.get("endpoint") or change.description),
            description=str(data.get("description") or change.description),
            team=change.team,
            details=data,
            updated_at=change.timestamp,
        )
        state.api = _upsert(state.api, endpoint, lambda item: f"{item.method} {item.path}")

    elif change.type == ChangeType.api_removed:
        method = str(data.get("method", "GET")).upper()
        path = str(data.get("path") or data.get("endpoint") or change.description)
        state.api = [item for item in state.api if f"{item.method} {item.path}" != f"{method} {path}"]

    elif change.type in {ChangeType.requirement_added, ChangeType.requirement_changed}:
        requirement_id = str(data.get("id") or slugify(change.description))
        requirement = Requirement(
            id=requirement_id,
            title=str(data.get("title") or change.description),
            description=str(data.get("description") or change.description),
            status=str(data.get("status") or "open"),
            team=change.team,
            details=data,
            updated_at=change.timestamp,
        )
        state.requirements = _upsert(state.requirements, requirement, lambda item: item.id)

    elif change.type == ChangeType.requirement_closed:
        requirement_id = str(data.get("id") or slugify(change.description))
        state.requirements = [
            item.model_copy(update={"status": "closed", "updated_at": change.timestamp})
            if item.id == requirement_id
            else item
            for item in state.requirements
        ]

    elif change.type == ChangeType.component_spec:
        component = ComponentSpec(
            name=str(data.get("name") or data.get("component") or change.description),
            spec=str(data.get("spec") or data.get("description") or change.description),
            team=change.team,
            details=data,
            updated_at=change.timestamp,
        )
        state.components = _upsert(state.components, component, lambda item: item.name.lower())

    state.api.sort(key=lambda item: (item.path, item.method))
    state.requirements.sort(key=lambda item: (item.status != "open", item.updated_at), reverse=True)
    state.components.sort(key=lambda item: item.name.lower())
    return state


def rebuild_state(
    project: str,
    changes: list[Change],
    *,
    project_id: str = "",
    subprojects: list[SubprojectRecord] | None = None,
) -> ProjectState:
    state = empty_state(project, project_id=project_id, subprojects=subprojects)
    for change in sorted(changes, key=lambda item: item.version):
        state = apply_change(state, change)
    state.recent_changes = sorted(changes, key=lambda item: item.version, reverse=True)[:20]
    state.recent_digest = build_digest(state.recent_changes)
    state.subprojects = list(subprojects or state.subprojects)
    return state


def build_digest(changes: list[Change]) -> str:
    if not changes:
        return "No changes have been published yet."

    recent = [change for change in changes if change.timestamp >= datetime.now(UTC) - timedelta(days=1)]
    scope = recent or changes[:10]
    by_team = Counter(change.team.value for change in scope)
    by_type = Counter(change.type.value for change in scope)

    lines = [
        f"{len(scope)} recent change{'s' if len(scope) != 1 else ''}: "
        + ", ".join(f"{count} from {team}" for team, count in by_team.items()),
        "Top update types: " + ", ".join(f"{kind} ({count})" for kind, count in by_type.most_common(3)),
        "Latest: " + scope[0].description,
    ]
    return "\n".join(lines)


def format_state_markdown(state: ProjectState) -> str:
    lines = [
        f"# {state.project} shared state",
        "",
        f"Project ID: `{state.project_id}`",
        f"Version: {state.version}",
        f"Updated: {state.updated_at.isoformat()}",
        "",
        "## Subprojects",
    ]
    if state.subprojects:
        lines.extend(f"- {item.team.value}: {item.status.value}" for item in state.subprojects)
    else:
        lines.append("- None onboarded yet")
    lines.extend(["", "## Digest", state.recent_digest, "", "## API endpoints"])
    lines.extend(f"- `{item.method} {item.path}`: {item.description}" for item in state.api)
    lines.append("")
    lines.append("## Open requirements")
    lines.extend(f"- {item.title} ({item.team})" for item in state.requirements if item.status == "open")
    lines.append("")
    lines.append("## Components")
    lines.extend(f"- {item.name}: {item.spec}" for item in state.components)
    return "\n".join(lines)


def _upsert(items: list[T], item: T, key: Callable[[T], str]) -> list[T]:
    item_key = key(item)
    replaced = False
    next_items: list[T] = []
    for existing in items:
        if key(existing) == item_key:
            next_items.append(item)
            replaced = True
        else:
            next_items.append(existing)
    if not replaced:
        next_items.append(item)
    return next_items
