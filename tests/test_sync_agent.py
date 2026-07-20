from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from sync_agent.config import AgentMode, AgentSettings
from sync_agent.prompts import crawl_prompt
from sync_agent.report import build_mcp_servers
from sync_agent.runner import run_loop, run_once
from sync_agent.watch import GitWatchError, read_head_sha
from tests.conftest import login_headers, make_app


def test_crawl_prompt_backend_mentions_openapi():
    text = crawl_prompt(project_header="adra-backend", project_id="adra", team="backend", openapi_url="http://x/openapi.json")
    assert "import_openapi" in text
    assert "http://x/openapi.json" in text


def test_crawl_prompt_frontend_mentions_snapshot():
    text = crawl_prompt(project_header="adra-frontend", project_id="adra", team="frontend")
    assert "import_snapshot" in text
    assert "get_latest_state" in text


def test_agent_settings_parse_project_and_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "adra-frontend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_HUB_URL", "http://192.168.17.29:8080/mcp")
    settings = AgentSettings()
    settings.validate_required()
    assert settings.project_name_and_team() == ("adra", "frontend")
    assert settings.resolve_rest_base() == "http://192.168.17.29:8080"
    servers = build_mcp_servers(settings)
    assert servers["team-sync"]["headers"]["Project"] == "adra"
    assert servers["team-sync"]["headers"]["Team"] == "frontend"
    assert servers["team-sync"]["headers"]["Authorization"] == "Bearer sk_test"


def test_read_head_sha_real_repo():
    # This workspace is a git repo.
    sha = read_head_sha(".")
    assert len(sha) >= 7


def test_read_head_sha_missing_path(tmp_path: Path):
    with pytest.raises(GitWatchError):
        read_head_sha(tmp_path / "nope")


def test_run_once_mocked_agent_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "demo-backend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_MODE", "once")
    settings = AgentSettings()

    def fake_prompt(**kwargs):
        assert kwargs["cwd"] == str(tmp_path.resolve())
        assert "team-sync" in kwargs["mcp_servers"]
        return SimpleNamespace(status="finished", result="ok")

    with patch("sync_agent.runner.report_agent_status") as report:
        code = run_once(settings, agent_prompt=fake_prompt, commit_sha="abc")
        assert code == 0
        assert report.call_count >= 2
        assert report.call_args_list[-1].kwargs["status"] == "ok"


def test_invoke_agent_uses_async_bridge_when_forced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "demo-backend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_FORCE_ASYNC_BRIDGE", "1")
    settings = AgentSettings()

    fake_sdk = SimpleNamespace(
        Agent=SimpleNamespace(prompt=lambda *a, **k: None),
        AgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        HttpMcpServerConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        LocalAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    with (
        patch.dict("sys.modules", {"cursor_sdk": fake_sdk}),
        patch("sync_agent.runner._invoke_agent_async", return_value=SimpleNamespace(status="finished")) as async_invoke,
        patch("sync_agent.runner.report_agent_status"),
    ):
        from sync_agent.runner import _invoke_agent

        result = _invoke_agent(
            settings,
            prompt="hi",
            cwd=str(tmp_path),
            mcp_servers={"team-sync": {"url": "http://x/mcp", "headers": {}}},
            agent_prompt=None,
        )
        assert result.status == "finished"
        async_invoke.assert_called_once()


def test_report_agent_status_warns_on_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    import logging

    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "adra-frontend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_HUB_URL", "http://192.168.17.29:8080/mcp")
    settings = AgentSettings()

    class FakeResponse:
        status_code = 404
        text = '{"detail":"Not Found"}'

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch("sync_agent.report.sync_client_for", return_value=FakeClient()):
        with caplog.at_level(logging.WARNING):
            from sync_agent.report import report_agent_status

            report_agent_status(settings, status="ok")
    assert any("outdated" in r.message for r in caplog.records)


def test_report_agent_status_uses_split_project_team_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "adra")
    monkeypatch.setenv("SYNC_AGENT_TEAM", "frontend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_HUB_URL", "http://192.168.17.29:8080/mcp")
    settings = AgentSettings()

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured["team"] = kwargs.get("json", {}).get("team", "")
            return FakeResponse()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    with patch("sync_agent.report.sync_client_for", return_value=FakeClient()):
        from sync_agent.report import report_agent_status

        report_agent_status(settings, status="ok")

    assert captured["url"].endswith("/api/projects/adra/agent-status")
    assert captured["team"] == "frontend"


def test_on_commit_skips_unchanged_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNC_AGENT_API_KEY", "sk_test")
    monkeypatch.setenv("SYNC_AGENT_PROJECT", "demo-frontend")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_key")
    monkeypatch.setenv("SYNC_AGENT_CWD", str(tmp_path))
    monkeypatch.setenv("SYNC_AGENT_MODE", "on_commit")
    monkeypatch.setenv("SYNC_AGENT_INTERVAL_SECONDS", "30")
    settings = AgentSettings()

    calls = {"n": 0}

    def fake_prompt(**kwargs):
        calls["n"] += 1
        return SimpleNamespace(status="finished")

    # Force exit after one unchanged sleep by patching sleep and raising KeyboardInterrupt on second sleep.
    sleeps = {"n": 0}

    def fake_sleep(_seconds: int) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise KeyboardInterrupt()

    with (
        patch("sync_agent.runner.read_head_sha", return_value="same"),
        patch("sync_agent.runner.sleep_interval", side_effect=fake_sleep),
        patch("sync_agent.runner.report_agent_status"),
    ):
        # Seed last_sha by first successful run then skip
        # run_loop starts with last_sha=""; first iteration sees change and runs once, then skip, then interrupt
        code = run_loop(settings, agent_prompt=fake_prompt)
        assert code == 0
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_agent_status_endpoint(tmp_path: Path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await login_headers(client)
            project = (await client.post("/api/projects", headers=headers, json={"name": "adra"})).json()
            resp = await client.post(
                f"/api/projects/{project['id']}/agent-status",
                headers=headers,
                json={"team": "frontend", "status": "ok", "commit_sha": "deadbeef"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["team"] == "frontend"
            assert body["last_agent_status"] == "ok"
            assert body["last_agent_sha"] == "deadbeef"

            state = (await client.get(f"/api/projects/{project['id']}/state", headers=headers)).json()
            fe = next(s for s in state["subprojects"] if s["team"] == "frontend")
            assert fe["last_agent_status"] == "ok"
