from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from sync_mcp.api import create_api_router
from sync_mcp.config import Settings, get_settings
from sync_mcp.mcp import create_mcp_server
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.storage import create_store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = create_store(settings)
    notifier = ChangeNotifier()
    mcp = create_mcp_server(store, notifier, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.init()
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Team Sync MCP Server", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def protect_mcp(request: Request, call_next):
        if settings.token and request.url.path.startswith("/mcp"):
            expected = f"Bearer {settings.token}"
            if request.headers.get("authorization") != expected:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Missing or invalid bearer token"}, status_code=401)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/dashboard/")

    app.include_router(create_api_router(store, notifier, settings), prefix="/api")
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
