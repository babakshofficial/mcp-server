from __future__ import annotations

from abc import ABC, abstractmethod

from sync_mcp.models import (
    ApiKeyCreated,
    ApiKeyRecord,
    AuthPrincipal,
    Change,
    ChangeCreate,
    HubRole,
    HubSettings,
    HubSettingsUpdate,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectSummary,
    ProjectState,
    ProjectUpdate,
    SnapshotImport,
    SubprojectRecord,
    SubprojectStatus,
    Team,
    User,
    UserPublic,
)


class StateStore(ABC):
    @abstractmethod
    async def init(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_projects(self) -> list[ProjectSummary]:
        raise NotImplementedError

    @abstractmethod
    async def list_projects_for_user(self, user_id: str, *, is_admin: bool) -> list[ProjectSummary]:
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
        owner_user_id: str | None = None,
        teams: list[str] | None = None,
    ) -> Project:
        raise NotImplementedError

    @abstractmethod
    async def update_project(self, project_id: str, update: ProjectUpdate) -> Project:
        raise NotImplementedError

    @abstractmethod
    async def delete_project(self, project_id: str) -> None:
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

    @abstractmethod
    async def update_agent_status(
        self,
        project_id: str,
        team: Team,
        *,
        status: str,
        error: str = "",
        commit_sha: str = "",
    ) -> SubprojectRecord:
        raise NotImplementedError

    # --- Auth / RBAC ---

    @abstractmethod
    async def count_users(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        hub_role: HubRole = HubRole.member,
    ) -> User:
        raise NotImplementedError

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None:
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_username(self, username: str) -> User | None:
        raise NotImplementedError

    @abstractmethod
    async def get_user_password_hash(self, user_id: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def list_users(self) -> list[UserPublic]:
        raise NotImplementedError

    @abstractmethod
    async def update_user(
        self,
        user_id: str,
        *,
        hub_role: HubRole | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> User:
        raise NotImplementedError

    @abstractmethod
    async def list_project_members(self, project_id: str) -> list[ProjectMember]:
        raise NotImplementedError

    @abstractmethod
    async def set_project_member(
        self,
        project_id: str,
        user_id: str,
        role: ProjectRole,
    ) -> ProjectMember:
        raise NotImplementedError

    @abstractmethod
    async def remove_project_member(self, project_id: str, user_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_project_role(self, project_id: str, user_id: str) -> ProjectRole | None:
        raise NotImplementedError

    @abstractmethod
    async def create_api_key(
        self,
        user_id: str,
        name: str,
        prefix: str,
        key_hash: str,
        *,
        key_id: str | None = None,
    ) -> ApiKeyRecord:
        raise NotImplementedError

    @abstractmethod
    async def list_api_keys(self, user_id: str | None = None) -> list[ApiKeyRecord]:
        raise NotImplementedError

    @abstractmethod
    async def revoke_api_key(self, key_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def find_api_key_by_prefix(self, prefix: str) -> tuple[ApiKeyRecord, str] | None:
        """Return key record and key_hash for active (non-revoked) keys matching prefix."""
        raise NotImplementedError

    @abstractmethod
    async def touch_api_key(self, key_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def count_api_keys(self) -> int:
        raise NotImplementedError
