from __future__ import annotations

from pathlib import Path

import httpx

from sync_mcp.app import create_app
from sync_mcp.config import Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    data = {
        "project": "hub",
        "data_dir": tmp_path,
        "dashboard_dist": tmp_path / "dist",
        "secret": "test-secret-key-for-jwt-32bytes!!",
        "admin_username": "admin",
        "admin_password": "adminpass",
        "token": "",
    }
    data.update(overrides)
    return Settings(**data)


async def login_headers(client: httpx.AsyncClient, username: str = "admin", password: str = "adminpass") -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def make_app(tmp_path: Path, **overrides):
    return create_app(make_settings(tmp_path, **overrides))
