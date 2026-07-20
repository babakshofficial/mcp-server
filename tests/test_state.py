from datetime import UTC, datetime

from sync_mcp.models import Change, ChangeType, Team, Teams
from sync_mcp.state import rebuild_state, slugify


def test_rebuild_state_tracks_api_requirements_and_components():
    changes = [
        Change(
            version=1,
            project_id="demo",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            team=Teams.backend,
            type=ChangeType.api_added,
            description="Add user lookup",
            details={"method": "GET", "path": "/users/:id"},
        ),
        Change(
            version=2,
            project_id="demo",
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            team=Teams.frontend,
            type=ChangeType.requirement_added,
            description="User payload needs avatar_url",
            details={"id": "user-avatar", "title": "Expose avatar_url"},
        ),
        Change(
            version=3,
            project_id="demo",
            timestamp=datetime(2026, 1, 3, tzinfo=UTC),
            team=Teams.frontend,
            type=ChangeType.component_spec,
            description="UserCard",
            details={"name": "UserCard", "spec": "Shows name, email, and avatar."},
        ),
    ]

    state = rebuild_state("demo", changes, project_id="demo")

    assert state.project_id == "demo"
    assert state.version == 3
    assert state.api[0].path == "/users/:id"
    assert state.requirements[0].id == "user-avatar"
    assert state.components[0].name == "UserCard"
    assert "recent change" in state.recent_digest


def test_rebuild_state_removes_api_and_closes_requirements():
    changes = [
        Change(
            version=1,
            project_id="demo",
            team=Teams.backend,
            type=ChangeType.api_added,
            description="Add old endpoint",
            details={"method": "GET", "path": "/legacy"},
        ),
        Change(
            version=2,
            project_id="demo",
            team=Teams.backend,
            type=ChangeType.api_removed,
            description="Remove old endpoint",
            details={"method": "GET", "path": "/legacy"},
        ),
        Change(
            version=3,
            project_id="demo",
            team=Teams.frontend,
            type=ChangeType.requirement_added,
            description="Need export",
            details={"id": "export"},
        ),
        Change(
            version=4,
            project_id="demo",
            team=Teams.backend,
            type=ChangeType.requirement_closed,
            description="Need export",
            details={"id": "export"},
        ),
    ]

    state = rebuild_state("demo", changes, project_id="demo")

    assert state.api == []
    assert state.requirements[0].status == "closed"


def test_slugify():
    assert slugify("Acme App") == "acme-app"
    assert slugify("!!!") == "project"
