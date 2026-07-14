from __future__ import annotations

from abc import ABC, abstractmethod

from sync_mcp.models import (
    Change,
    ChangeCreate,
    HubSettings,
    HubSettingsUpdate,
    Project,
    ProjectSummary,
    ProjectState,
    ProjectUpdate,
    SnapshotImport,
    SubprojectRecord,
    SubprojectStatus,
    Team,
)


class StateStore(ABC):
    @abstractmethod
    async def init(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_projects(self) -> list[ProjectSummary]:
        raise NotImplementedError

    @abstractmethod
    async def get_project(self, project_id: str) -> Project | None:
        raise NotImplementedError

    @abstractmethod
    async def create_project(
        self,
        name: str,
        description: str = "",
        *,
        openapi_url: str = "",
        auto_sync: bool = True,
        sync_mode: str = "interval",
        git_repo_path: str = "",
    ) -> Project:
        raise NotImplementedError

    @abstractmethod
    async def update_project(self, project_id: str, update: ProjectUpdate) -> Project:
        raise NotImplementedError

    @abstractmethod
    async def find_project_by_name_or_id(self, name_or_id: str) -> Project | None:
        raise NotImplementedError

    @abstractmethod
    async def list_auto_sync_targets(self) -> list[Project]:
        raise NotImplementedError

    @abstractmethod
    async def update_sync_status(
        self,
        project_id: str,
        *,
        status: str,
        error: str = "",
        fingerprint: str | None = None,
        last_git_sha: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_hub_settings(self) -> HubSettings:
        raise NotImplementedError

    @abstractmethod
    async def update_hub_settings(self, update: HubSettingsUpdate) -> HubSettings:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, project_id: str, change: ChangeCreate) -> tuple[Change, ProjectState]:
        raise NotImplementedError

    @abstractmethod
    async def get_state(self, project_id: str) -> ProjectState:
        raise NotImplementedError

    @abstractmethod
    async def get_changelog(
        self,
        project_id: str,
        *,
        since: str | None = None,
        team: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[Change]:
        raise NotImplementedError

    @abstractmethod
    async def import_snapshot(
        self,
        project_id: str,
        snapshot: SnapshotImport,
    ) -> tuple[Change, ProjectState]:
        raise NotImplementedError

    @abstractmethod
    async def mark_subproject(
        self,
        project_id: str,
        team: Team,
        status: SubprojectStatus,
        summary: str = "",
    ) -> SubprojectRecord:
        raise NotImplementedError

    @abstractmethod
    async def get_subprojects(self, project_id: str) -> list[SubprojectRecord]:
        raise NotImplementedError
