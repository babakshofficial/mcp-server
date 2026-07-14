from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from sync_mcp.models import AuthPrincipal, HubRole


def issue_jwt(*, secret: str, user_id: str, username: str, hub_role: HubRole, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "hub_role": hub_role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_jwt(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def principal_from_jwt_payload(payload: dict[str, Any]) -> AuthPrincipal:
    return AuthPrincipal(
        user_id=str(payload["sub"]),
        username=str(payload.get("username") or ""),
        hub_role=HubRole(payload.get("hub_role") or HubRole.member.value),
        auth_via="jwt",
    )
