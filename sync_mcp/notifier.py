from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from sync_mcp.models import Change, ProjectState


class ChangeNotifier:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def publish(self, change: Change, state: ProjectState) -> None:
        payload = json.dumps(
            {
                "change": change.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
            }
        )
        for queue in list(self._subscribers):
            queue.put_nowait(payload)

    async def stream(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"event: change\ndata: {payload}\n\n"
                except TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            self._subscribers.discard(queue)
