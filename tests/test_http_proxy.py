from __future__ import annotations

import pytest

from sync_mcp.http_proxy import (
    async_client_for,
    is_local_or_private_host,
    resolve_proxy_url,
    sync_client_for,
)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("api.localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("192.168.17.29", True),
        ("10.0.0.5", True),
        ("172.16.0.1", True),
        ("169.254.1.1", True),
        ("petstore.swagger.io", False),
        ("example.com", False),
        ("", True),
    ],
)
def test_is_local_or_private_host(host: str, expected: bool) -> None:
    assert is_local_or_private_host(host) is expected


def test_resolve_proxy_url_bypasses_lan_even_when_proxy_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MCP_HTTP_PROXY", "http://proxy.corp:8080")
    monkeypatch.setenv("SYNC_MCP_HTTPS_PROXY", "http://proxy.corp:8080")
    assert resolve_proxy_url("http://192.168.17.29:8001/openapi.json") is None
    assert resolve_proxy_url("http://127.0.0.1:8000/openapi.json") is None


def test_resolve_proxy_url_uses_proxy_for_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MCP_HTTP_PROXY", "http://proxy.corp:8080")
    monkeypatch.setenv("SYNC_MCP_HTTPS_PROXY", "http://proxy.corp:8443")
    assert (
        resolve_proxy_url("https://petstore.swagger.io/v2/swagger.json")
        == "http://proxy.corp:8443"
    )


def test_resolve_proxy_url_prefixed_env_wins_over_standard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MCP_HTTP_PROXY", "http://prefixed:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://standard:8080")
    assert resolve_proxy_url("http://example.com/openapi.json") == "http://prefixed:8080"


def test_resolve_proxy_url_falls_back_to_standard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNC_MCP_HTTP_PROXY", raising=False)
    monkeypatch.delenv("SYNC_MCP_HTTPS_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://standard:3128")
    assert resolve_proxy_url("http://example.com/openapi.json") == "http://standard:3128"


def test_async_client_for_uses_resolved_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MCP_HTTPS_PROXY", "http://proxy.corp:8080")
    client = async_client_for("https://example.com/spec.json", timeout=5.0)
    assert resolve_proxy_url("https://example.com/spec.json") == "http://proxy.corp:8080"
    assert client._trust_env is False  # noqa: SLF001


def test_sync_client_for_no_proxy_for_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MCP_HTTP_PROXY", "http://proxy.corp:8080")
    assert resolve_proxy_url("http://192.168.1.10:8000/openapi.json") is None
    with sync_client_for("http://192.168.1.10:8000/openapi.json") as client:
        assert client._trust_env is False  # noqa: SLF001
