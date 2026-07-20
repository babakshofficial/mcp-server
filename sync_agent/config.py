from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentMode(StrEnum):
    once = "once"
    schedule = "schedule"
    on_commit = "on_commit"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNC_AGENT_", env_file=".env", extra="ignore")

    hub_url: str = Field(
        default="http://127.0.0.1:8080/mcp",
        description="Team Sync Streamable HTTP MCP URL",
    )
    api_key: str = Field(default="", description="Team Sync API key (sk_...)")
    project: str = Field(
        default="",
        description="Project id/name, or legacy name-team / name/team",
    )
    team: str = Field(
        default="",
        description="Optional Team header (preferred with Project=id)",
    )
    cwd: Path = Field(default=Path("."), description="Local checkout to crawl")
    cursor_api_key: str = Field(default="", description="Cursor SDK API key")
    model: str = "composer-2.5"
    mode: AgentMode = AgentMode.once
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    openapi_url: str = ""
    rest_base: str = Field(
        default="",
        description="Hub REST base for agent status reporting (default: derived from hub_url)",
    )

    def resolve_cursor_api_key(self) -> str:
        import os

        return (self.cursor_api_key or os.environ.get("CURSOR_API_KEY") or "").strip()

    def resolve_rest_base(self) -> str:
        if self.rest_base.strip():
            return self.rest_base.rstrip("/")
        # http://host:8080/mcp -> http://host:8080
        url = self.hub_url.rstrip("/")
        if url.endswith("/mcp"):
            return url[: -len("/mcp")]
        return url

    def project_name_and_team(self) -> tuple[str, str]:
        from sync_mcp.models import normalize_team
        from sync_mcp.project_context import parse_project_and_team_headers

        raw = self.project.strip()
        team_env = (self.team or "").strip()
        try:
            if team_env:
                return parse_project_and_team_headers(raw, team_env)
            return parse_project_and_team_headers(raw, None)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def validate_required(self) -> None:
        if not self.api_key.strip():
            raise ValueError("SYNC_AGENT_API_KEY is required")
        if not self.project.strip():
            raise ValueError("SYNC_AGENT_PROJECT is required")
        if not self.resolve_cursor_api_key():
            raise ValueError("CURSOR_API_KEY or SYNC_AGENT_CURSOR_API_KEY is required")
        self.project_name_and_team()
        cwd = self.cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"SYNC_AGENT_CWD is not a directory: {cwd}")
