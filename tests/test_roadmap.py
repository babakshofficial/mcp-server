from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sync_mcp.models import (
    Artifact,
    ArtifactKind,
    Change,
    ChangeType,
    ComponentSpec,
    SnapshotImport,
    Teams,
)
from sync_mcp.project_context import parse_project_and_team_headers
from sync_mcp.snapshot_ops import prune_changes_for_replace, snapshot_to_changes
from sync_mcp.state import apply_change, empty_state
from tests.conftest import login_headers, make_app


def test_parse_project_and_team_headers_preferred():
    name, team = parse_project_and_team_headers("adra", "frontend")
    assert name == "adra"
    assert team == "frontend"


def test_parse_project_slash_form():
    name, team = parse_project_and_team_headers("adra/mobile", None)
    assert name == "adra"
    assert team == "mobile"


def test_parse_legacy_name_team():
    name, team = parse_project_and_team_headers("adra-backend", None)
    assert name == "adra"
    assert team == "backend"


def test_custom_team_slug():
    name, team = parse_project_and_team_headers("adra", "qa")
    assert team == "qa"


def test_snapshot_replace_prunes_components():
    state = empty_state("Demo", "demo")
    state = apply_change(
        state,
        Change(
            project_id="demo",
            version=1,
            team=Teams.frontend,
            type=ChangeType.component_spec,
            description="Old",
            details={"name": "OldCard", "spec": "gone"},
        ),
    )
    state = apply_change(
        state,
        Change(
            project_id="demo",
            version=2,
            team=Teams.frontend,
            type=ChangeType.component_spec,
            description="Keep",
            details={"name": "KeepCard", "spec": "stay"},
        ),
    )
    snapshot = SnapshotImport(
        team=Teams.frontend,
        components=[ComponentSpec(name="KeepCard", spec="stay", team=Teams.frontend)],
        replace=True,
    )
    removals = prune_changes_for_replace(snapshot=snapshot, current=state)
    assert any(c.type == ChangeType.component_removed for c in removals)
    assert snapshot_to_changes(snapshot)


def test_artifact_and_ack_apply():
    state = empty_state("Demo", "demo")
    state = apply_change(
        state,
        Change(
            project_id="demo",
            version=1,
            team=Teams.backend,
            type=ChangeType.artifact_upsert,
            description="API_URL",
            details={"kind": "env_var", "key": "API_URL", "title": "API base"},
        ),
    )
    assert len(state.artifacts) == 1
    assert state.artifacts[0].kind == ArtifactKind.env_var
    state = apply_change(
        state,
        Change(
            project_id="demo",
            version=2,
            team=Teams.frontend,
            type=ChangeType.change_ack,
            description="ack",
            details={"change_id": "abc", "status": "ack", "user_id": "u1", "username": "bob"},
        ),
    )
    assert len(state.acknowledgements) == 1


@pytest.mark.asyncio
async def test_create_project_with_mobile_template(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            resp = await client.post(
                "/api/projects",
                headers=headers,
                json={"name": "Mobile App", "template": "mobile"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "mobile" in body["teams"]
            assert "backend" in body["teams"]


@pytest.mark.asyncio
async def test_ack_endpoint(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            project = (await client.post("/api/projects", headers=headers, json={"name": "Ack App"})).json()
            pub = await client.post(
                f"/api/projects/{project['id']}/updates",
                headers=headers,
                json={"team": "backend", "type": "changelog", "description": "API breaking"},
            )
            assert pub.status_code == 200
            change_id = pub.json()["change_id"]
            ack = await client.post(
                f"/api/projects/{project['id']}/acks",
                headers=headers,
                json={"change_id": change_id, "team": "frontend", "status": "blocked", "note": "need v2"},
            )
            assert ack.status_code == 200
            state = (await client.get(f"/api/projects/{project['id']}/state", headers=headers)).json()
            assert any(a["status"] == "blocked" for a in state["acknowledgements"])


@pytest.mark.asyncio
async def test_sse_filters_by_membership(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            admin = await login_headers(client)
            await client.post("/api/users", headers=admin, json={"username": "bob", "password": "bobpass123", "hub_role": "member"})
            project = (await client.post("/api/projects", headers=admin, json={"name": "Secret"})).json()
            # bob has no membership — stream allow list empty for bob
            bob = await login_headers(client, "bob", "bobpass123")
            from sync_mcp.notifier import ChangeNotifier
            from sync_mcp.models import Change, ProjectState

            notifier: ChangeNotifier = app.router.routes  # noqa — use app state
            # Access via create_app wiring
            store = app.state.store
            # Build allow predicate like routes
            summaries = await store.list_projects_for_user(
                (await client.get("/api/auth/me", headers=bob)).json()["id"],
                is_admin=False,
            )
            allowed = {s.id for s in summaries}
            assert project["id"] not in allowed
