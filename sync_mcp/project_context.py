from __future__ import annotations

import contextvars
from dataclasses import dataclass

from sync_mcp.models import AuthPrincipal, Team

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
    """Parse `name-type` where type is the last hyphen segment."""
    raw = value.strip()
    if not raw or "-" not in raw:
        raise ValueError("Project header must look like '<project_name>-<project_type>'")
    name, type_part = raw.rsplit("-", 1)
    name = name.strip()
    type_part = type_part.strip().lower()
    if not name:
        raise ValueError("Project header missing project name")
    try:
        team = Team(type_part)
    except ValueError as exc:
        raise ValueError("Project type must be backend, frontend, or other") from exc
    return name, team
