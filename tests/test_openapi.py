from sync_mcp.openapi_import import endpoints_from_openapi
from sync_mcp.models import Team, Teams


def test_endpoints_from_openapi():
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/users/{id}": {
                "get": {
                    "summary": "Get user",
                    "operationId": "get_user",
                    "responses": {"200": {"description": "ok"}},
                },
                "delete": {
                    "summary": "Delete user",
                    "responses": {"204": {"description": "gone"}},
                },
            },
            "/health": {
                "get": {"summary": "Health"},
            },
        },
    }
    endpoints = endpoints_from_openapi(spec, team=Teams.backend)
    assert {(e.method, e.path) for e in endpoints} == {
        ("GET", "/users/{id}"),
        ("DELETE", "/users/{id}"),
        ("GET", "/health"),
    }
    get_user = next(e for e in endpoints if e.method == "GET" and e.path == "/users/{id}")
    assert get_user.description == "Get user"
    assert get_user.details["source"] == "openapi"
