from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from sync_mcp.config import Settings
from sync_mcp.models import ChangeCreate, PublishResult
from sync_mcp.notifier import ChangeNotifier
from sync_mcp.storage.base import StateStore


def create_api_router(store: StateStore, notifier: ChangeNotifier, settings: Settings) -> APIRouter:
    router = APIRouter()

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.token:
            return
        expected = f"Bearer {settings.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "project": settings.project}

    @router.get("/state")
    async def state():
        return await store.get_state()

    @router.get("/changelog")
    async def changelog(
        since: str | None = None,
        team: str | None = None,
        type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return await store.get_changelog(since=since, team=team, change_type=type, limit=limit)

    @router.post("/updates", dependencies=[Depends(require_token)])
    async def publish(change: ChangeCreate) -> PublishResult:
        saved, next_state = await store.publish(change)
        await notifier.publish(saved, next_state)
        return PublishResult(change_id=saved.id, version=saved.version, state=next_state)

    @router.get("/events")
    async def events():
        return StreamingResponse(notifier.stream(), media_type="text/event-stream")

    return router
