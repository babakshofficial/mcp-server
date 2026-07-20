from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass

from sync_mcp.models import AuthPrincipal, Team, normalize_team

_project_context: contextvars.ContextVar[ProjectHeaderContext | None] = contextvars.ContextVar(
    "sync_mcp_project_context",
    default=None,
)
_principal_context: contextvars.ContextVar[AuthPrincipal | None] = contextvars.ContextVar(
    "sync_mcp_principal_context",
    default=None,
)


@dataclass(frozen=True)
class ProjectHeaderContext:
    project_id: str
    team: Team
    raw: str


def set_project_context(ctx: ProjectHeaderContext | None) -> contextvars.Token:
    return _project_context.set(ctx)


def reset_project_context(token: contextvars.Token) -> None:
    _project_context.reset(token)


def get_project_context() -> ProjectHeaderContext | None:
    return _project_context.get()


def set_principal_context(principal: AuthPrincipal | None) -> contextvars.Token:
    return _principal_context.set(principal)


def reset_principal_context(token: contextvars.Token) -> None:
    _principal_context.reset(token)


def get_principal_context() -> AuthPrincipal | None:
    return _principal_context.get()


def parse_project_header(value: str) -> tuple[str, Team]:
    """Parse legacy `name-team` where team is the last hyphen segment."""
    raw = value.strip()
    if not raw or "-" not in raw:
        raise ValueError(
            "Project header must look like '<project_name>-<team>' "
            "(or use separate Project + Team headers)"
        )
    name, type_part = raw.rsplit("-", 1)
    name = name.strip()
    if not name:
        raise ValueError("Project header missing project name")
    try:
        team = normalize_team(type_part)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return name, team


def parse_project_and_team_headers(
    project_header: str | None,
    team_header: str | None,
) -> tuple[str, Team]:
    """Resolve project name/id and team from Project + optional Team headers.

    Preferred: ``Project: adra`` + ``Team: frontend``
    Also: ``Project: adra/frontend``
    Legacy: ``Project: adra-frontend`` (when Team header is absent)
    """
    project_raw = (project_header or "").strip()
    team_raw = (team_header or "").strip()
    if not project_raw:
        raise ValueError(
            "Project header required. Prefer Project: <id> with Team: <team>, "
            "or legacy Project: <name>-<team>."
        )

    if team_raw:
        return project_raw, normalize_team(team_raw)

    if "/" in project_raw:
        name, team_part = project_raw.split("/", 1)
        name = name.strip()
        if not name:
            raise ValueError("Project header missing project id before '/'")
        return name, normalize_team(team_part)

    # Legacy name-team only when the suffix is a valid team slug
    if "-" in project_raw:
        name, maybe_team = project_raw.rsplit("-", 1)
        name = name.strip()
        maybe_team = maybe_team.strip().lower()
        if name and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", maybe_team):
            try:
                return name, normalize_team(maybe_team)
            except ValueError:
                pass

    raise ValueError(
        "Team header required when Project is not '<name>-<team>' or '<name>/<team>'. "
        "Example: Project: adra and Team: frontend"
    )
