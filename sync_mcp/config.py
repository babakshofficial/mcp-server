from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNC_MCP_", env_file=".env", extra="ignore")

    project: str = Field(default="my-project", description="Display name for this shared state hub.")
    token: str = Field(default="", description="Optional shared bearer token for write/MCP access.")
    storage: Literal["sqlite", "json"] = "sqlite"
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8080
    dashboard_dist: Path = Path("dashboard/dist")

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "sync_mcp.sqlite3"

    @property
    def json_path(self) -> Path:
        return self.data_dir / "sync_mcp.json"


def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
