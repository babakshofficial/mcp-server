from sync_mcp.models import ApiEndpoint, ChangeType, Team
from sync_mcp.openapi_diff import diff_openapi_changes, openapi_fingerprint


def test_diff_detects_added_changed_removed():
    current = [
        ApiEndpoint(method="GET", path="/a", description="A", team=Team.backend, details={"source": "openapi"}),
        ApiEndpoint(method="GET", path="/b", description="old", team=Team.backend, details={"source": "openapi"}),
    ]
    discovered = [
        ApiEndpoint(method="GET", path="/b", description="new", team=Team.backend, details={"source": "openapi"}),
        ApiEndpoint(method="POST", path="/c", description="C", team=Team.backend, details={"source": "openapi"}),
    ]
    changes = diff_openapi_changes(current, discovered)
    types = {change.type for change in changes}
    assert ChangeType.api_removed in types
    assert ChangeType.api_changed in types
    assert ChangeType.api_added in types
    assert openapi_fingerprint(discovered) != openapi_fingerprint(current)
