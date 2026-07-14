from pathlib import Path

import aiosqlite
import httpx
import pytest

from sync_mcp.app import create_app
from sync_mcp.config import Settings
from sync_mcp.models import ApiEndpoint, ChangeCreate, ChangeType, SnapshotImport, Team
from sync_mcp.storage.sqlite_store import SQLiteStateStore


@pytest.mark.asyncio
async def test_multi_project_isolation_and_snapshot(tmp_path: Path):
    settings = Settings(project="hub", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            alpha = (await client.post("/api/projects", json={"name": "Alpha App", "description": "A"})).json()
            beta = (await client.post("/api/projects", json={"name": "Beta App"})).json()

            snap = await client.post(
                f"/api/projects/{alpha['id']}/snapshot",
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
                json={
                    "team": "frontend",
                    "type": "component_spec",
                    "description": "CheckoutForm",
                    "details": {"name": "CheckoutForm", "spec": "Collects payment details"},
                },
            )

            alpha_state = (await client.get(f"/api/projects/{alpha['id']}/state")).json()
            beta_state = (await client.get(f"/api/projects/{beta['id']}/state")).json()
            assert alpha_state["api"][0]["path"] == "/users"
            assert alpha_state["components"] == []
            assert beta_state["components"][0]["name"] == "CheckoutForm"
            assert beta_state["api"] == []

            listing = (await client.get("/api/projects")).json()
            assert {item["id"] for item in listing} == {alpha["id"], beta["id"]}

            missing = await client.get("/api/state")
            assert missing.status_code == 400


@pytest.mark.asyncio
async def test_write_endpoints_respect_optional_token(tmp_path: Path):
    settings = Settings(project="hub", token="secret", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked = await client.post("/api/projects", json={"name": "Secure"})
            assert blocked.status_code == 401

            created = await client.post(
                "/api/projects",
                headers={"Authorization": "Bearer secret"},
                json={"name": "Secure"},
            )
            assert created.status_code == 200
            project_id = created.json()["id"]

            blocked_update = await client.post(
                f"/api/projects/{project_id}/updates",
                json={"team": "frontend", "type": "requirement_added", "description": "Need avatar_url"},
            )
            assert blocked_update.status_code == 401

            allowed = await client.post(
                f"/api/projects/{project_id}/updates",
                headers={"Authorization": "Bearer secret"},
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
            team=Team.backend,
            api=[ApiEndpoint(method="POST", path="/checkout", description="Create checkout")],
            notes="Reviewed routes.py",
        ),
    )
    assert state.api[0].path == "/checkout"
    assert state.subprojects[0].team == Team.backend
    assert state.subprojects[0].status.value == "ready"

    _, state2 = await store.publish(
        project.id,
        ChangeCreate(team=Team.frontend, type=ChangeType.requirement_added, description="Need tax field", details={"id": "tax"}),
    )
    assert any(item.id == "tax" for item in state2.requirements)


@pytest.mark.asyncio
async def test_openapi_rest_import(tmp_path: Path):
    settings = Settings(project="hub", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            project = (await client.post("/api/projects", json={"name": "API App"})).json()
            response = await client.post(
                f"/api/projects/{project['id']}/openapi",
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
    settings = Settings(project="hub", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            hub = (await client.get("/api/settings")).json()
            assert hub["poll_interval_seconds"] == 30
            assert hub["auto_sync_enabled"] is True

            updated = await client.put("/api/settings", json={"poll_interval_seconds": 15, "auto_sync_enabled": True})
            assert updated.status_code == 200
            assert updated.json()["poll_interval_seconds"] == 15

            project = (
                await client.post(
                    "/api/projects",
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
                json={"openapi_url": "http://example.com/v2/openapi.json"},
            )
            assert patched.status_code == 200
            assert patched.json()["openapi_url"].endswith("/v2/openapi.json")


@pytest.mark.asyncio
async def test_mcp_streamable_http_path_is_not_double_mounted(tmp_path: Path):
    settings = Settings(project="hub", data_dir=tmp_path, dashboard_dist=tmp_path / "dist")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bogus = await client.post("/mcp/mcp")
            assert bogus.status_code == 404

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
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            ok = await client.post("/mcp", json=payload, headers=headers)
            assert ok.status_code != 404
            assert ok.status_code < 500
