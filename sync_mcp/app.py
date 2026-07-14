from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from sync_mcp.api import create_api_router
from sync_mcp.autosync import AutoSyncService
from sync_mcp.config import Settings, get_settings
from sync_mcp.mcp import create_mcp_server
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.project_context import (
    ProjectHeaderContext,
    parse_project_header,
    reset_project_context,
    set_project_context,
)
from sync_mcp.storage import create_store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = create_store(settings)
    notifier = ChangeNotifier()
    mcp = create_mcp_server(store, notifier, settings)
    autosync = AutoSyncService(store, notifier)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.init()
        app.state.autosync = autosync
        autosync.start()
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await autosync.stop()

    app = FastAPI(title="Team Sync MCP Server", version="0.1.0", lifespan=lifespan)
    app.state.autosync = autosync
    app.state.store = store

    @app.middleware("http")
    async def protect_mcp(request: Request, call_next):
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)

        if settings.token:
            expected = f"Bearer {settings.token}"
            if request.headers.get("authorization") != expected:
                return JSONResponse({"detail": "Missing or invalid bearer token"}, status_code=401)

        project_header = request.headers.get("project") or request.headers.get("Project")
        token = set_project_context(None)
        try:
            if project_header:
                try:
                    name, team = parse_project_header(project_header)
                except ValueError as exc:
                    return JSONResponse({"detail": str(exc)}, status_code=400)
                project = await store.find_project_by_name_or_id(name)
                if project is None:
                    return JSONResponse(
                        {"detail": f"Unknown project in Project header: {name}"},
                        status_code=404,
                    )
                token = set_project_context(
                    ProjectHeaderContext(project_id=project.id, team=team, raw=project_header)
                )
            elif settings.token:
                return JSONResponse(
                    {
                        "detail": (
                            "Project header required. Use Project: <project_name>-<project_type> "
                            "(e.g. adra-backend)."
                        )
                    },
                    status_code=400,
                )
            return await call_next(request)
        finally:
            reset_project_context(token)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/dashboard/")

    app.include_router(create_api_router(store, notifier, settings, autosync), prefix="/api")
    app.mount("/mcp", mcp.streamable_http_app())
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_dashboard_dir(settings)), html=True, check_dir=False),
        name="dashboard",
    )
    return app


def _dashboard_dir(settings: Settings) -> Path:
    return settings.dashboard_dist if settings.dashboard_dist.is_absolute() else Path.cwd() / settings.dashboard_dist


app = create_app()
