from pathlib import Path

import httpx
import pytest

from sync_mcp.app import create_app
from sync_mcp.config import Settings


@pytest.mark.asyncio
async def test_publish_update_and_read_state(tmp_path: Path):
    settings = Settings(project="demo", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/updates",
                json={
                    "team": "backend",
                    "type": "api_added",
                    "description": "Add user lookup",
                    "details": {"method": "GET", "path": "/users/:id"},
                },
            )
            assert response.status_code == 200

            state = (await client.get("/api/state")).json()
            assert state["version"] == 1
            assert state["api"][0]["path"] == "/users/:id"

            changelog = (await client.get("/api/changelog?since=0&type=api_added")).json()
            assert len(changelog) == 1
            assert changelog[0]["team"] == "backend"


@pytest.mark.asyncio
async def test_write_endpoint_respects_optional_token(tmp_path: Path):
    settings = Settings(project="demo", token="secret", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post(
                "/api/updates",
                json={"team": "frontend", "type": "requirement_added", "description": "Need avatar_url"},
            )
            assert blocked.status_code == 401

            allowed = await client.post(
                "/api/updates",
                headers={"Authorization": "Bearer secret"},
                json={"team": "frontend", "type": "requirement_added", "description": "Need avatar_url"},
            )
            assert allowed.status_code == 200
