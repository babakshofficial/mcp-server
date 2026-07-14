from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Team(StrEnum):
    frontend = "frontend"
    backend = "backend"
    other = "other"


class ChangeType(StrEnum):
    api_added = "api_added"
    api_changed = "api_changed"
    api_removed = "api_removed"
    requirement_added = "requirement_added"
    requirement_changed = "requirement_changed"
    requirement_closed = "requirement_closed"
    component_spec = "component_spec"
    changelog = "changelog"
    other = "other"


class SubprojectStatus(StrEnum):
    pending = "pending"
    ready = "ready"


class SyncMode(StrEnum):
    interval = "interval"
    on_commit = "on_commit"


class HubRole(StrEnum):
    admin = "admin"
    member = "member"


class ProjectRole(StrEnum):
    owner = "owner"
    editor = "editor"
    viewer = "viewer"


PROJECT_ROLE_RANK = {
    ProjectRole.viewer: 1,
    ProjectRole.editor: 2,
    ProjectRole.owner: 3,
}


class ApiEndpoint(BaseModel):
    method: str = "GET"
    path: str
    description: str = ""
    team: Team = Team.backend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Requirement(BaseModel):
    id: str
    title: str
    description: str = ""
    status: str = "open"
    team: Team = Team.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComponentSpec(BaseModel):
    name: str
    spec: str = ""
    team: Team = Team.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    team: Team = Team.other
    type: ChangeType = ChangeType.other
    description: str
    details: dict[str, Any] = Field(default_factory=dict)


class Change(ChangeCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = ""
    version: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubprojectRecord(BaseModel):
    team: Team
    status: SubprojectStatus = SubprojectStatus.pending
    summary: str = ""
    onboarded_at: datetime | None = None


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    openapi_url: str = ""
    auto_sync: bool = True
    sync_mode: SyncMode = SyncMode.interval
    git_repo_path: str = ""
    last_git_sha: str = ""
    last_sync_at: datetime | None = None
    last_sync_status: str = ""
    last_sync_error: str = ""
    openapi_fingerprint: str = ""


class ProjectState(BaseModel):
    project_id: str = ""
    project: str
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    api: list[ApiEndpoint] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)
    recent_changes: list[Change] = Field(default_factory=list)
    recent_digest: str = "No changes have been published yet."
    subprojects: list[SubprojectRecord] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    version: int = 0
    updated_at: datetime
    open_requirements: int = 0
    api_count: int = 0
    component_count: int = 0
    subprojects: list[SubprojectRecord] = Field(default_factory=list)
    recent_digest: str = ""
    openapi_url: str = ""
    auto_sync: bool = True
    sync_mode: SyncMode = SyncMode.interval
    git_repo_path: str = ""
    last_git_sha: str = ""
    last_sync_at: datetime | None = None
    last_sync_status: str = ""
    last_sync_error: str = ""


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    openapi_url: str = ""
    auto_sync: bool = True
    sync_mode: SyncMode = SyncMode.interval
    git_repo_path: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    openapi_url: str | None = None
    auto_sync: bool | None = None
    sync_mode: SyncMode | None = None
    git_repo_path: str | None = None


class HubSettings(BaseModel):
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    auto_sync_enabled: bool = True


class HubSettingsUpdate(BaseModel):
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    auto_sync_enabled: bool | None = None


class SnapshotImport(BaseModel):
    team: Team
    api: list[ApiEndpoint] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    notes: str = ""


class PublishResult(BaseModel):
    change_id: str
    project_id: str
    version: int
    state: ProjectState


class SnapshotResult(BaseModel):
    project_id: str
    team: Team
    change_id: str
    version: int
    state: ProjectState


class User(BaseModel):
    id: str
    username: str
    hub_role: HubRole = HubRole.member
    disabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserPublic(BaseModel):
    id: str
    username: str
    hub_role: HubRole
    disabled: bool = False
    created_at: datetime


class UserCreate(BaseModel):
    username: str
    password: str
    hub_role: HubRole = HubRole.member


class UserUpdate(BaseModel):
    hub_role: HubRole | None = None
    disabled: bool | None = None
    password: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: UserPublic


class ProjectMember(BaseModel):
    project_id: str
    user_id: str
    username: str = ""
    role: ProjectRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectMemberUpdate(BaseModel):
    user_id: str | None = None
    username: str | None = None
    role: ProjectRole


class ApiKeyRecord(BaseModel):
    id: str
    user_id: str
    name: str
    prefix: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreate(BaseModel):
    name: str = "default"
    user_id: str | None = None


class ApiKeyCreated(BaseModel):
    key: ApiKeyRecord
    raw_key: str


class AuthPrincipal(BaseModel):
    user_id: str
    username: str
    hub_role: HubRole
    auth_via: str = "jwt"
    api_key_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.hub_role == HubRole.admin
