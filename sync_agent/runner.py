from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from sync_agent.config import AgentMode, AgentSettings
from sync_agent.prompts import crawl_prompt
from sync_agent.report import build_mcp_servers, report_agent_status
from sync_agent.watch import GitWatchError, read_head_sha, sleep_interval

logger = logging.getLogger(__name__)


def run_once(
    settings: AgentSettings,
    *,
    prompt_fn: Callable[..., str] = crawl_prompt,
    agent_prompt: Callable[..., Any] | None = None,
    commit_sha: str = "",
) -> int:
    """Run a single Cursor SDK crawl. Returns process exit code."""
    settings.validate_required()
    project_id, team = settings.project_name_and_team()
    # project_id in tools is the hub slug; header name segment is used as best-effort id.
    prompt = prompt_fn(
        project_header=settings.project,
        project_id=project_id,
        team=team,
        openapi_url=settings.openapi_url,
    )
    mcp_servers = build_mcp_servers(settings)
    cwd = str(settings.cwd.expanduser().resolve())

    report_agent_status(settings, status="running", commit_sha=commit_sha)
    try:
        result = _invoke_agent(
            settings,
            prompt=prompt,
            cwd=cwd,
            mcp_servers=mcp_servers,
            agent_prompt=agent_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent startup/run failed: %s", exc)
        report_agent_status(settings, status="error", error=str(exc), commit_sha=commit_sha)
        return 1

    status = getattr(result, "status", None) or (result.get("status") if isinstance(result, dict) else None)
    if status == "error":
        detail = getattr(result, "result", None) or str(result)
        logger.error("Agent run finished with error: %s", detail)
        report_agent_status(settings, status="error", error=str(detail)[:500], commit_sha=commit_sha)
        return 2

    logger.info("Agent run finished: status=%s", status)
    report_agent_status(settings, status="ok", commit_sha=commit_sha)
    return 0


def _invoke_agent(
    settings: AgentSettings,
    *,
    prompt: str,
    cwd: str,
    mcp_servers: dict[str, Any],
    agent_prompt: Callable[..., Any] | None,
) -> Any:
    if agent_prompt is not None:
        return agent_prompt(prompt=prompt, settings=settings, cwd=cwd, mcp_servers=mcp_servers)

    from cursor_sdk import Agent, AgentOptions, HttpMcpServerConfig, LocalAgentOptions

    servers = {
        name: HttpMcpServerConfig(url=cfg["url"], headers=cfg.get("headers"))
        for name, cfg in mcp_servers.items()
    }
    return Agent.prompt(
        prompt,
        AgentOptions(
            api_key=settings.resolve_cursor_api_key(),
            model=settings.model,
            local=LocalAgentOptions(cwd=cwd),
            mcp_servers=servers,
        ),
    )


def run_loop(settings: AgentSettings, *, agent_prompt: Callable[..., Any] | None = None) -> int:
    settings.validate_required()
    last_sha = ""
    mode = settings.mode

    if mode == AgentMode.once:
        sha = ""
        try:
            sha = read_head_sha(settings.cwd)
        except GitWatchError:
            pass
        return run_once(settings, agent_prompt=agent_prompt, commit_sha=sha)

    while True:
        try:
            if mode == AgentMode.on_commit:
                try:
                    sha = read_head_sha(settings.cwd)
                except GitWatchError as exc:
                    logger.warning("%s", exc)
                    sleep_interval(settings.interval_seconds)
                    continue
                if sha == last_sha:
                    logger.debug("HEAD unchanged (%s); sleeping", sha[:12])
                    sleep_interval(min(30, settings.interval_seconds))
                    continue
                logger.info("HEAD changed %s -> %s; running agent", last_sha[:12] or "(none)", sha[:12])
                code = run_once(settings, agent_prompt=agent_prompt, commit_sha=sha)
                if code == 0:
                    last_sha = sha
                sleep_interval(min(30, settings.interval_seconds))
            else:
                # schedule
                sha = ""
                try:
                    sha = read_head_sha(settings.cwd)
                except GitWatchError:
                    pass
                logger.info("Scheduled crawl starting")
                run_once(settings, agent_prompt=agent_prompt, commit_sha=sha)
                sleep_interval(settings.interval_seconds)
        except KeyboardInterrupt:
            logger.info("Stopped")
            return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    settings = AgentSettings()
    try:
        settings.validate_required()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    return run_loop(settings)
