from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.conftest import login_headers, make_app


@pytest.mark.asyncio
async def test_login_success_and_fail(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            assert bad.status_code == 401

            ok = await client.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
            assert ok.status_code == 200
            body = ok.json()
            assert body["user"]["username"] == "admin"
            assert body["user"]["hub_role"] == "admin"
            assert body["token"]


@pytest.mark.asyncio
async def test_admin_can_delete_project_viewer_cannot_sync(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            admin_headers = await login_headers(client)
            project = (
                await client.post(
                    "/api/projects",
                    headers=admin_headers,
                    json={"name": "RBAC App", "openapi_url": "http://example.com/openapi.json"},
                )
            ).json()

            # Create viewer member
            viewer = await client.post(
                "/api/users",
                headers=admin_headers,
                json={"username": "viewer1", "password": "viewerpass", "hub_role": "member"},
            )
            assert viewer.status_code == 200
            viewer_id = viewer.json()["id"]
            await client.put(
                f"/api/projects/{project['id']}/members",
                headers=admin_headers,
                json={"user_id": viewer_id, "role": "viewer"},
            )

            viewer_login = await client.post(
                "/api/auth/login",
                json={"username": "viewer1", "password": "viewerpass"},
            )
            viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['token']}"}

            forbidden_sync = await client.post(
                f"/api/projects/{project['id']}/sync",
                headers=viewer_headers,
            )
            assert forbidden_sync.status_code == 403

            forbidden_delete = await client.delete(
                f"/api/projects/{project['id']}",
                headers=viewer_headers,
            )
            assert forbidden_delete.status_code == 403

            deleted = await client.delete(f"/api/projects/{project['id']}", headers=admin_headers)
            assert deleted.status_code == 200
            assert deleted.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_member_cannot_delete_without_ownership(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            admin_headers = await login_headers(client)
            project = (await client.post("/api/projects", headers=admin_headers, json={"name": "Owned"})).json()

            editor = await client.post(
                "/api/users",
                headers=admin_headers,
                json={"username": "editor1", "password": "editorpass", "hub_role": "member"},
            )
            editor_id = editor.json()["id"]
            await client.put(
                f"/api/projects/{project['id']}/members",
                headers=admin_headers,
                json={"user_id": editor_id, "role": "editor"},
            )

            editor_login = await client.post(
                "/api/auth/login",
                json={"username": "editor1", "password": "editorpass"},
            )
            editor_headers = {"Authorization": f"Bearer {editor_login.json()['token']}"}

            # Editor can publish
            publish = await client.post(
                f"/api/projects/{project['id']}/updates",
                headers=editor_headers,
                json={"team": "backend", "type": "other", "description": "note"},
            )
            assert publish.status_code == 200

            # Editor cannot delete
            delete = await client.delete(f"/api/projects/{project['id']}", headers=editor_headers)
            assert delete.status_code == 403


@pytest.mark.asyncio
async def test_api_key_scopes_mcp_project(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            admin_headers = await login_headers(client)
            await client.post("/api/projects", headers=admin_headers, json={"name": "adra"})
            other = (await client.post("/api/projects", headers=admin_headers, json={"name": "other"})).json()

            member = await client.post(
                "/api/users",
                headers=admin_headers,
                json={"username": "dev", "password": "devpass", "hub_role": "member"},
            )
            member_id = member.json()["id"]
            await client.put(
                "/api/projects/adra/members",
                headers=admin_headers,
                json={"user_id": member_id, "role": "editor"},
            )

            # Mint API key as admin for member
            key_resp = await client.post(
                "/api/api-keys",
                headers=admin_headers,
                json={"name": "dev-cursor", "user_id": member_id},
            )
            raw_key = key_resp.json()["raw_key"]

            # Member key can access adra
            ok = await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {raw_key}",
                    "Project": "adra-backend",
                    "Content-Type": "application/json",
                },
                json={},
            )
            # 400 may be from empty MCP body but auth/project passed; not 403
            assert ok.status_code != 403
            assert ok.status_code != 401

            # Member key cannot access other project
            denied = await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {raw_key}",
                    "Project": "other-backend",
                    "Content-Type": "application/json",
                },
                json={},
            )
            assert denied.status_code == 403

            # List projects as member JWT — only adra
            login = await client.post("/api/auth/login", json={"username": "dev", "password": "devpass"})
            member_headers = {"Authorization": f"Bearer {login.json()['token']}"}
            listing = (await client.get("/api/projects", headers=member_headers)).json()
            assert {p["id"] for p in listing} == {"adra"}
            assert other["id"] not in {p["id"] for p in listing}
