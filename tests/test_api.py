from pathlib import Path

import aiosqlite
import httpx
import pytest

from sync_mcp.config import Settings
from sync_mcp.models import ApiEndpoint, ChangeCreate, ChangeType, SnapshotImport, Team, Teams
from sync_mcp.storage.sqlite_store import SQLiteStateStore
from tests.conftest import login_headers, make_app


@pytest.mark.asyncio
async def test_multi_project_isolation_and_snapshot(tmp_path: Path):
    app = make_app(tmp_path)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            alpha = (await client.post("/api/projects", headers=headers, json={"name": "Alpha App", "description": "A"})).json()
            beta = (await client.post("/api/projects", headers=headers, json={"name": "Beta App"})).json()

            snap = await client.post(
                f"/api/projects/{alpha['id']}/snapshot",
                headers=headers,
                json={
                    "team": "backend",
                    "api": [{"method": "GET", "path": "/users", "description": "List users"}],
                    "components": [],
                    "requirements": [],
                    "notes": "Scanned FastAPI routes",
                },
            )
            assert snap.status_code == 200
            assert snap.json()["state"]["api"][0]["path"] == "/users"
            assert any(item["team"] == "backend" and item["status"] == "ready" for item in snap.json()["state"]["subprojects"])

            await client.post(
                f"/api/projects/{beta['id']}/updates",
                headers=headers,
                json={
                    "team": "frontend",
                    "type": "component_spec",
                    "description": "CheckoutForm",
                    "details": {"name": "CheckoutForm", "spec": "Collects payment details"},
                },
            )

            alpha_state = (await client.get(f"/api/projects/{alpha['id']}/state", headers=headers)).json()
            beta_state = (await client.get(f"/api/projects/{beta['id']}/state", headers=headers)).json()
            assert alpha_state["api"][0]["path"] == "/users"
            assert alpha_state["components"] == []
            assert beta_state["components"][0]["name"] == "CheckoutForm"
            assert beta_state["api"] == []

            listing = (await client.get("/api/projects", headers=headers)).json()
            assert {item["id"] for item in listing} == {alpha["id"], beta["id"]}

            missing = await client.get("/api/state", headers=headers)
            assert missing.status_code == 400


@pytest.mark.asyncio
async def test_writes_require_authentication(tmp_path: Path):
    app = make_app(tmp_path)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post("/api/projects", json={"name": "Secure"})
            assert blocked.status_code == 401

            headers = await login_headers(client)
            created = await client.post("/api/projects", headers=headers, json={"name": "Secure"})
            assert created.status_code == 200
            project_id = created.json()["id"]

            blocked_update = await client.post(
                f"/api/projects/{project_id}/updates",
                json={"team": "frontend", "type": "requirement_added", "description": "Need avatar_url"},
            )
            assert blocked_update.status_code == 401

            allowed = await client.post(
                f"/api/projects/{project_id}/updates",
                headers=headers,
                json={"team": "frontend", "type": "requirement_added", "description": "Need avatar_url"},
            )
            assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_legacy_sqlite_migration(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite3"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE changes (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                team TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE aggregated_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            INSERT INTO changes (id, version, timestamp, team, type, description, details)
            VALUES ('c1', 1, '2026-01-01T00:00:00+00:00', 'backend', 'api_added', 'Users', '{"method":"GET","path":"/users"}')
            """
        )
        await db.execute(
            "INSERT INTO aggregated_state (id, state) VALUES (1, ?)",
            ('{"project":"legacy-app","version":1,"updated_at":"2026-01-01T00:00:00+00:00","api":[],"requirements":[],"components":[],"recent_changes":[],"recent_digest":"x"}',),
        )
        await db.commit()

    store = SQLiteStateStore(db_path, "legacy-app")
    await store.init()
    projects = await store.list_projects()
    assert len(projects) == 1
    assert projects[0].id == "legacy-app"
    state = await store.get_state("legacy-app")
    assert state.version == 1
    assert state.api[0].path == "/users"


@pytest.mark.asyncio
async def test_store_import_snapshot_marks_subproject(tmp_path: Path):
    store = SQLiteStateStore(tmp_path / "sync.sqlite3", "hub")
    await store.init()
    project = await store.create_project("Checkout")
    _, state = await store.import_snapshot(
        project.id,
        SnapshotImport(
            team=Teams.backend,
            api=[ApiEndpoint(method="POST", path="/checkout", description="Create checkout")],
            notes="Reviewed routes.py",
        ),
    )
    assert state.api[0].path == "/checkout"
    assert state.subprojects[0].team == Teams.backend
    assert state.subprojects[0].status.value == "ready"

    _, state2 = await store.publish(
        project.id,
        ChangeCreate(team=Teams.frontend, type=ChangeType.requirement_added, description="Need tax field", details={"id": "tax"}),
    )
    assert any(item.id == "tax" for item in state2.requirements)


@pytest.mark.asyncio
async def test_openapi_rest_import(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            project = (await client.post("/api/projects", headers=headers, json={"name": "API App"})).json()
            response = await client.post(
                f"/api/projects/{project['id']}/openapi",
                headers=headers,
                json={
                    "openapi": {
                        "paths": {
                            "/items": {"get": {"summary": "List items"}, "post": {"summary": "Create item"}},
                        }
                    },
                    "notes": "from FastAPI openapi.json",
                },
            )
            assert response.status_code == 200
            paths = {(e["method"], e["path"]) for e in response.json()["state"]["api"]}
            assert paths == {("GET", "/items"), ("POST", "/items")}


@pytest.mark.asyncio
async def test_hub_settings_and_project_openapi_config(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            hub = (await client.get("/api/settings", headers=headers)).json()
            assert hub["poll_interval_seconds"] == 30
            assert hub["auto_sync_enabled"] is True

            updated = await client.put(
                "/api/settings",
                headers=headers,
                json={"poll_interval_seconds": 15, "auto_sync_enabled": True},
            )
            assert updated.status_code == 200
            assert updated.json()["poll_interval_seconds"] == 15

            project = (
                await client.post(
                    "/api/projects",
                    headers=headers,
                    json={
                        "name": "Polled",
                        "openapi_url": "http://example.com/openapi.json",
                        "auto_sync": True,
                    },
                )
            ).json()
            assert project["openapi_url"].endswith("openapi.json")

            patched = await client.patch(
                f"/api/projects/{project['id']}",
                headers=headers,
                json={"openapi_url": "http://example.com/v2/openapi.json"},
            )
            assert patched.status_code == 200
            assert patched.json()["openapi_url"].endswith("/v2/openapi.json")


@pytest.mark.asyncio
async def test_mcp_streamable_http_path_is_not_double_mounted(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bogus = await client.post("/mcp/mcp")
            # Auth middleware runs for any /mcp* path before routing.
            assert bogus.status_code in {401, 404}

            # Create API key for MCP auth
            headers = await login_headers(client)
            key_resp = await client.post("/api/api-keys", headers=headers, json={"name": "mcp"})
            assert key_resp.status_code == 200
            raw_key = key_resp.json()["raw_key"]
            await client.post("/api/projects", headers=headers, json={"name": "adra"})

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
            mcp_headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {raw_key}",
                "Project": "adra-backend",
            }
            ok = await client.post("/mcp", json=payload, headers=mcp_headers)
            assert ok.status_code != 404
            assert ok.status_code < 500

            # Double mount path should not succeed as a second MCP endpoint with auth.
            double = await client.post("/mcp/mcp", json=payload, headers=mcp_headers)
            assert double.status_code in {404, 405}
