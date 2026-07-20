from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from sync_mcp.models import Change, ProjectState


class ChangeNotifier:
    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.Queue[str], Callable[[str], bool]]] = set()

    async def publish(self, change: Change, state: ProjectState) -> None:
        project_id = state.project_id or change.project_id
        payload = json.dumps(
            {
                "project_id": project_id,
                "change": change.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
            }
        )
        for queue, allow in list(self._subscribers):
            if allow(project_id):
                queue.put_nowait(payload)

    async def stream(self, *, allow_project: Callable[[str], bool] | None = None) -> AsyncIterator[str]:
        """Yield SSE frames. allow_project filters change events by project_id (admins: always True)."""
        predicate = allow_project or (lambda _pid: True)
        queue: asyncio.Queue[str] = asyncio.Queue()
        entry = (queue, predicate)
        self._subscribers.add(entry)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"event: change\ndata: {payload}\n\n"
                except TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            self._subscribers.discard(entry)
