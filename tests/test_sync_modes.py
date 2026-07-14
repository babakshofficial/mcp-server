from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sync_mcp.autosync import AutoSyncService
from sync_mcp.models import Team
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.project_context import parse_project_header
from sync_mcp.storage.sqlite_store import SQLiteStateStore
from tests.conftest import login_headers, make_app


def test_parse_project_header_name_and_type():
    name, team = parse_project_header("adra-backend")
    assert name == "adra"
    assert team == Team.backend

    name, team = parse_project_header("my-app-frontend")
    assert name == "my-app"
    assert team == Team.frontend


def test_parse_project_header_rejects_invalid():
    with pytest.raises(ValueError):
        parse_project_header("notype")
    with pytest.raises(ValueError):
        parse_project_header("app-mobile")
    with pytest.raises(ValueError):
        parse_project_header("-backend")


@pytest.mark.asyncio
async def test_on_commit_skips_openapi_when_sha_unchanged(tmp_path: Path):
    store = SQLiteStateStore(tmp_path / "test.sqlite3", "hub")
    await store.init()
    project = await store.create_project(
        "Demo",
        openapi_url="http://openapi.test/openapi.json",
        auto_sync=True,
        sync_mode="on_commit",
        git_repo_path=str(tmp_path / "repo"),
    )
    await store.update_sync_status(project.id, status="ok", last_git_sha="abc123")
    project = await store.get_project(project.id)
    assert project is not None

    service = AutoSyncService(store, ChangeNotifier())
    with patch("sync_mcp.autosync.read_head_sha", new=AsyncMock(return_value="abc123")) as read_sha:
        with patch.object(service, "sync_project", new=AsyncMock()) as sync:
            outcome = await service.maybe_sync_project(project)
            assert outcome == "skipped"
            sync.assert_not_awaited()
            read_sha.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_commit_triggers_sync_when_sha_changes(tmp_path: Path):
    store = SQLiteStateStore(tmp_path / "test.sqlite3", "hub")
    await store.init()
    project = await store.create_project(
        "Demo",
        openapi_url="http://openapi.test/openapi.json",
        auto_sync=True,
        sync_mode="on_commit",
        git_repo_path=str(tmp_path / "repo"),
    )
    await store.update_sync_status(project.id, status="ok", last_git_sha="oldsha")
    project = await store.get_project(project.id)
    assert project is not None

    service = AutoSyncService(store, ChangeNotifier())
    with patch("sync_mcp.autosync.read_head_sha", new=AsyncMock(return_value="newsha")):
        with patch.object(service, "sync_project", new=AsyncMock(return_value=True)) as sync:
            outcome = await service.maybe_sync_project(project)
            assert outcome == "updated"
            sync.assert_awaited_once()
            kwargs = sync.await_args.kwargs
            assert kwargs.get("commit_sha") == "newsha"
            assert kwargs.get("trigger") == "on_commit"


@pytest.mark.asyncio
async def test_commit_webhook_calls_sync(tmp_path: Path):
    app = make_app(tmp_path)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            await client.put(
                "/api/settings",
                headers=headers,
                json={"auto_sync_enabled": False},
            )
            created = await client.post(
                "/api/projects",
                headers=headers,
                json={
                    "name": "Webhook App",
                    "openapi_url": "http://openapi.test/openapi.json",
                    "auto_sync": False,
                },
            )
            assert created.status_code == 200
            project_id = created.json()["id"]

            with patch.object(
                app.state.autosync,
                "sync_project",
                new=AsyncMock(return_value=True),
            ) as sync:
                response = await client.post(
                    f"/api/projects/{project_id}/hooks/commit",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"commit_sha": "deadbeef"},
                )
            assert response.status_code == 200
            body = response.json()
            assert body["changed"] is True
            assert body["commit_sha"] == "deadbeef"
            sync.assert_awaited_once()
            assert sync.await_args.kwargs.get("trigger") == "commit_hook"
            assert sync.await_args.kwargs.get("commit_sha") == "deadbeef"


@pytest.mark.asyncio
async def test_mcp_requires_auth_and_project_header(tmp_path: Path):
    app = make_app(tmp_path)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            key_resp = await client.post("/api/api-keys", headers=headers, json={"name": "mcp"})
            raw_key = key_resp.json()["raw_key"]
            await client.post("/api/projects", headers=headers, json={"name": "adra"})

            unauth = await client.post("/mcp", headers={"Content-Type": "application/json"}, json={})
            assert unauth.status_code == 401

            missing_project = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
                json={},
            )
            assert missing_project.status_code == 400
            assert "Project" in missing_project.json()["detail"]

            unknown = await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {raw_key}",
                    "Project": "missing-backend",
                    "Content-Type": "application/json",
                },
                json={},
            )
            assert unknown.status_code == 404
