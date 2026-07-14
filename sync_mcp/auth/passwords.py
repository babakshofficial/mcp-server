from __future__ import annotations

import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, prefix, key_hash). Raw key is shown once."""
    raw = f"sk_{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    key_hash = hash_password(raw)
    return raw, prefix, key_hash


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    return verify_password(raw_key, key_hash)
