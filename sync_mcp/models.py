from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from urllib.parse import urlparse


def normalize_team(value: str) -> str:
    """Validate a team slug (custom teams allowed)."""
    text = (value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", text):
        raise ValueError(
            "team must be a lowercase slug starting with a letter "
            "(e.g. frontend, backend, mobile, qa); max 32 chars"
        )
    return text


Team = Annotated[str, AfterValidator(normalize_team)]


class Teams:
    """Built-in team slug constants (custom slugs are also allowed)."""

    frontend = "frontend"
    backend = "backend"
    other = "other"


DEFAULT_PROJECT_TEAMS: list[str] = [Teams.frontend, Teams.backend, Teams.other]

PROJECT_TEMPLATES: dict[str, list[str]] = {
    "web": [Teams.frontend, Teams.backend],
    "mobile": ["mobile", Teams.backend],
    "monorepo": [Teams.frontend, Teams.backend, "docs", "qa"],
    "blank": [],
}


class ChangeType(StrEnum):
    api_added = "api_added"
    api_changed = "api_changed"
    api_removed = "api_removed"
    requirement_added = "requirement_added"
    requirement_changed = "requirement_changed"
    requirement_closed = "requirement_closed"
    component_spec = "component_spec"
    component_removed = "component_removed"
    artifact_upsert = "artifact_upsert"
    artifact_removed = "artifact_removed"
    change_ack = "change_ack"
    changelog = "changelog"
    other = "other"


class ArtifactKind(StrEnum):
    env_var = "env_var"
    feature_flag = "feature_flag"
    event = "event"
    error_code = "error_code"
    design_token = "design_token"
    runbook = "runbook"
    dependency = "dependency"
    other = "other"


class AckStatus(StrEnum):
    ack = "ack"
    blocked = "blocked"
    needs_version = "needs_version"


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
    team: Team = Teams.backend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Requirement(BaseModel):
    id: str
    title: str
    description: str = ""
    status: str = "open"
    team: Team = Teams.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComponentSpec(BaseModel):
    name: str
    spec: str = ""
    team: Team = Teams.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Artifact(BaseModel):
    kind: ArtifactKind = ArtifactKind.other
    key: str
    title: str = ""
    description: str = ""
    team: Team = Teams.other
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeAcknowledgement(BaseModel):
    change_id: str
    team: Team
    status: AckStatus
    note: str = ""
    user_id: str = ""
    username: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    team: Team = Teams.other
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
    last_agent_at: datetime | None = None
    last_agent_status: str = ""
    last_agent_error: str = ""
    last_agent_sha: str = ""


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
    teams: list[str] = Field(default_factory=lambda: list(DEFAULT_PROJECT_TEAMS))


class ProjectState(BaseModel):
    project_id: str = ""
    project: str
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    api: list[ApiEndpoint] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    acknowledgements: list[ChangeAcknowledgement] = Field(default_factory=list)
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
    artifact_count: int = 0
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
    teams: list[str] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    openapi_url: str = ""
    auto_sync: bool = True
    sync_mode: SyncMode = SyncMode.interval
    git_repo_path: str = ""
    template: str = "web"
    teams: list[str] | None = None

    @field_validator("openapi_url")
    @classmethod
    def validate_openapi_url(cls, value: str) -> str:
        return _validate_openapi_fetch_url(value)

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        key = (value or "web").strip().lower()
        if key not in PROJECT_TEMPLATES:
            raise ValueError(f"template must be one of: {', '.join(sorted(PROJECT_TEMPLATES))}")
        return key

    @field_validator("teams")
    @classmethod
    def validate_teams(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return [normalize_team(t) for t in value]


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    openapi_url: str | None = None
    auto_sync: bool | None = None
    sync_mode: SyncMode | None = None
    git_repo_path: str | None = None
    teams: list[str] | None = None

    @field_validator("openapi_url")
    @classmethod
    def validate_openapi_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_openapi_fetch_url(value)

    @field_validator("teams")
    @classmethod
    def validate_teams(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return [normalize_team(t) for t in value]


def _validate_openapi_fetch_url(value: str) -> str:
    """Reject bind addresses that cannot be used as HTTP fetch targets."""
    text = (value or "").strip()
    if not text:
        return ""
    host = (urlparse(text).hostname or "").lower()
    if host in {"0.0.0.0", "::", "[::]"}:
        raise ValueError(
            "openapi_url must be a reachable host, not a bind address like 0.0.0.0. "
            "Use the machine LAN IP (e.g. http://192.168.17.29:8001/openapi.json)."
        )
    return text


def resolve_project_teams(*, template: str = "web", teams: list[str] | None = None) -> list[str]:
    if teams is not None:
        return list(dict.fromkeys(teams))
    return list(PROJECT_TEMPLATES.get(template, DEFAULT_PROJECT_TEAMS))


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
    artifacts: list[Artifact] = Field(default_factory=list)
    notes: str = ""
    replace: bool = False


class ArtifactImport(BaseModel):
    team: Team = Teams.other
    artifacts: list[Artifact] = Field(default_factory=list)
    notes: str = ""


class ChangeAckCreate(BaseModel):
    change_id: str
    team: Team
    status: AckStatus
    note: str = ""


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
