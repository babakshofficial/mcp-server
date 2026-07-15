from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNC_MCP_", env_file=".env", extra="ignore")

    project: str = Field(default="my-project", description="Display name for this shared state hub.")
    token: str = Field(
        default="",
        description="Deprecated: migrated once into an admin API key when bootstrapping.",
    )
    secret: str = Field(
        default="",
        description="JWT signing secret. Auto-generated for the process if empty (dev only).",
    )
    admin_username: str = Field(default="", description="Bootstrap admin username when no users exist.")
    admin_password: str = Field(default="", description="Bootstrap admin password when no users exist.")
    storage: Literal["sqlite", "json"] = "sqlite"
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8080
    dashboard_dist: Path = Path("dashboard/dist")
    jwt_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, description="Dashboard JWT lifetime.")
    http_proxy: str = Field(
        default="",
        description="HTTP proxy for outbound public web fetches (falls back to HTTP_PROXY).",
    )
    https_proxy: str = Field(
        default="",
        description="HTTPS proxy for outbound public web fetches (falls back to HTTPS_PROXY, then http_proxy).",
    )

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "sync_mcp.sqlite3"

    @property
    def json_path(self) -> Path:
        return self.data_dir / "sync_mcp.json"

    def resolve_secret(self) -> str:
        if self.secret:
            return self.secret
        # Ephemeral secret for local/dev; set SYNC_MCP_SECRET in production.
        generated = secrets.token_urlsafe(32)
        object.__setattr__(self, "secret", generated)
        return generated


def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
