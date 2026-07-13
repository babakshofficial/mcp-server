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


class ApiEndpoint(BaseModel):
    method: str = "GET"
    path: str
    description: str = ""
    team: Team = Team.backend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class Requirement(BaseModel):
    id: str
    title: str
    description: str = ""
    status: str = "open"
    team: Team = Team.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class ComponentSpec(BaseModel):
    name: str
    spec: str = ""
    team: Team = Team.frontend
    details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class ChangeCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    team: Team = Team.other
    type: ChangeType = ChangeType.other
    description: str
    details: dict[str, Any] = Field(default_factory=dict)


class Change(ChangeCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectState(BaseModel):
    project: str
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    api: list[ApiEndpoint] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)
    recent_changes: list[Change] = Field(default_factory=list)
    recent_digest: str = "No changes have been published yet."


class PublishResult(BaseModel):
    change_id: str
    version: int
    state: ProjectState
