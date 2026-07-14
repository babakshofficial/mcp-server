from __future__ import annotations

import logging

from sync_mcp.auth.passwords import generate_api_key, hash_password, verify_api_key, verify_password
from sync_mcp.auth.tokens import decode_jwt, issue_jwt, principal_from_jwt_payload
from sync_mcp.config import Settings
from sync_mcp.models import AuthPrincipal, HubRole, User, UserPublic
from sync_mcp.storage.base import StateStore

logger = logging.getLogger(__name__)


async def bootstrap_auth(store: StateStore, settings: Settings) -> None:
    """Create first admin and migrate legacy SYNC_MCP_TOKEN into an API key."""
    if await store.count_users() == 0:
        username = (settings.admin_username or "").strip()
        password = settings.admin_password or ""
        if username and password:
            admin = await store.create_user(
                username,
                hash_password(password),
                hub_role=HubRole.admin,
            )
            logger.info("Bootstrapped admin user %s", admin.username)
        else:
            logger.warning(
                "No users exist. Set SYNC_MCP_ADMIN_USERNAME and SYNC_MCP_ADMIN_PASSWORD to create the first admin."
            )
            return

    if settings.token and await store.count_api_keys() == 0:
        admins = [u for u in await store.list_users() if u.hub_role == HubRole.admin and not u.disabled]
        if not admins:
            return
        raw = settings.token
        if not raw.startswith("sk_"):
            # Preserve exact legacy token string as the API key value.
            prefix = raw[:12] if len(raw) >= 12 else raw.ljust(12, "_")
            key_hash = hash_password(raw)
            await store.create_api_key(
                admins[0].id,
                "legacy-SYNC_MCP_TOKEN",
                prefix,
                key_hash,
            )
            logger.info("Migrated SYNC_MCP_TOKEN into an API key for admin %s (prefix %s)", admins[0].username, prefix)
        else:
            prefix = raw[:12]
            key_hash = hash_password(raw)
            await store.create_api_key(admins[0].id, "legacy-SYNC_MCP_TOKEN", prefix, key_hash)
            logger.info("Migrated SYNC_MCP_TOKEN API key for admin %s", admins[0].username)


async def authenticate_user(store: StateStore, username: str, password: str) -> User | None:
    user = await store.get_user_by_username(username)
    if user is None or user.disabled:
        return None
    password_hash = await store.get_user_password_hash(user.id)
    if not password_hash or not verify_password(password, password_hash):
        return None
    return user


def user_to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        username=user.username,
        hub_role=user.hub_role,
        disabled=user.disabled,
        created_at=user.created_at,
    )


def issue_access_token(settings: Settings, user: User) -> str:
    return issue_jwt(
        secret=settings.resolve_secret(),
        user_id=user.id,
        username=user.username,
        hub_role=user.hub_role,
        ttl_seconds=settings.jwt_ttl_seconds,
    )


async def resolve_bearer(store: StateStore, settings: Settings, authorization: str | None) -> AuthPrincipal | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None

    # Prefer JWT for dashboard sessions.
    try:
        payload = decode_jwt(token, settings.resolve_secret())
        principal = principal_from_jwt_payload(payload)
        user = await store.get_user(principal.user_id)
        if user is None or user.disabled:
            return None
        return AuthPrincipal(
            user_id=user.id,
            username=user.username,
            hub_role=user.hub_role,
            auth_via="jwt",
        )
    except Exception:  # noqa: BLE001
        pass

    # API keys: look up by prefix then verify hash.
    prefix = token[:12]
    found = await store.find_api_key_by_prefix(prefix)
    if found is None:
        # Legacy token may be shorter or not sk_-prefixed — scan active keys.
        for key in await store.list_api_keys():
            if key.revoked_at:
                continue
            pair = await store.find_api_key_by_prefix(key.prefix)
            if pair is None:
                continue
            record, key_hash = pair
            if verify_api_key(token, key_hash):
                user = await store.get_user(record.user_id)
                if user is None or user.disabled:
                    return None
                await store.touch_api_key(record.id)
                return AuthPrincipal(
                    user_id=user.id,
                    username=user.username,
                    hub_role=user.hub_role,
                    auth_via="api_key",
                    api_key_id=record.id,
                )
        return None

    record, key_hash = found
    if not verify_api_key(token, key_hash):
        return None
    user = await store.get_user(record.user_id)
    if user is None or user.disabled:
        return None
    await store.touch_api_key(record.id)
    return AuthPrincipal(
        user_id=user.id,
        username=user.username,
        hub_role=user.hub_role,
        auth_via="api_key",
        api_key_id=record.id,
    )


async def mint_api_key(store: StateStore, user_id: str, name: str) -> tuple[object, str]:
    raw, prefix, key_hash = generate_api_key()
    record = await store.create_api_key(user_id, name, prefix, key_hash)
    return record, raw
