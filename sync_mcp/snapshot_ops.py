from __future__ import annotations

from sync_mcp.models import (
    ChangeCreate,
    ChangeType,
    ProjectState,
    SnapshotImport,
)


def snapshot_to_changes(snapshot: SnapshotImport) -> list[ChangeCreate]:
    """Expand a snapshot into synthetic change creates (no digest)."""
    synthetic: list[ChangeCreate] = []
    for endpoint in snapshot.api:
        synthetic.append(
            ChangeCreate(
                team=snapshot.team,
                type=ChangeType.api_added,
                description=f"{endpoint.method} {endpoint.path}",
                details={
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "description": endpoint.description,
                    **endpoint.details,
                },
            )
        )
    for component in snapshot.components:
        synthetic.append(
            ChangeCreate(
                team=snapshot.team,
                type=ChangeType.component_spec,
                description=component.name,
                details={"name": component.name, "spec": component.spec, **component.details},
            )
        )
    for requirement in snapshot.requirements:
        synthetic.append(
            ChangeCreate(
                team=snapshot.team,
                type=ChangeType.requirement_added,
                description=requirement.title,
                details={
                    "id": requirement.id,
                    "title": requirement.title,
                    "description": requirement.description,
                    "status": requirement.status,
                    **requirement.details,
                },
            )
        )
    for artifact in snapshot.artifacts:
        synthetic.append(
            ChangeCreate(
                team=snapshot.team,
                type=ChangeType.artifact_upsert,
                description=artifact.title or artifact.key,
                details={
                    "kind": artifact.kind.value,
                    "key": artifact.key,
                    "title": artifact.title,
                    "description": artifact.description,
                    **artifact.details,
                },
            )
        )
    return synthetic


def prune_changes_for_replace(
    *,
    snapshot: SnapshotImport,
    current: ProjectState,
) -> list[ChangeCreate]:
    """When replace=True, emit removals for team-owned items missing from the snapshot."""
    if not snapshot.replace:
        return []
    team = snapshot.team
    keep_api = {f"{e.method.upper()} {e.path}" for e in snapshot.api}
    keep_components = {c.name.lower() for c in snapshot.components}
    keep_requirements = {r.id for r in snapshot.requirements}
    keep_artifacts = {f"{a.kind.value}:{a.key.lower()}" for a in snapshot.artifacts}

    removals: list[ChangeCreate] = []
    for endpoint in current.api:
        if endpoint.team != team:
            continue
        key = f"{endpoint.method.upper()} {endpoint.path}"
        if key not in keep_api:
            removals.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.api_removed,
                    description=f"Removed {key}",
                    details={"method": endpoint.method, "path": endpoint.path, "source": "snapshot_replace"},
                )
            )
    for component in current.components:
        if component.team != team:
            continue
        if component.name.lower() not in keep_components:
            removals.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.component_removed,
                    description=f"Removed component {component.name}",
                    details={"name": component.name, "source": "snapshot_replace"},
                )
            )
    for requirement in current.requirements:
        if requirement.team != team:
            continue
        if requirement.id not in keep_requirements and requirement.status != "closed":
            removals.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.requirement_closed,
                    description=f"Closed stale requirement {requirement.id}",
                    details={"id": requirement.id, "source": "snapshot_replace"},
                )
            )
    for artifact in current.artifacts:
        if artifact.team != team:
            continue
        key = f"{artifact.kind.value}:{artifact.key.lower()}"
        if key not in keep_artifacts:
            removals.append(
                ChangeCreate(
                    team=team,
                    type=ChangeType.artifact_removed,
                    description=f"Removed artifact {artifact.key}",
                    details={
                        "kind": artifact.kind.value,
                        "key": artifact.key,
                        "source": "snapshot_replace",
                    },
                )
            )
    return removals
