from __future__ import annotations

from abc import ABC, abstractmethod

from sync_mcp.models import Change, ChangeCreate, ProjectState


class StateStore(ABC):
    @abstractmethod
    async def init(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, change: ChangeCreate) -> tuple[Change, ProjectState]:
        raise NotImplementedError

    @abstractmethod
    async def get_state(self) -> ProjectState:
        raise NotImplementedError

    @abstractmethod
    async def get_changelog(
        self,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        raise NotImplementedError
