from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

import httpx


def is_local_or_private_host(host: str) -> bool:
    """True when the host should bypass HTTP(S) proxy (LAN, loopback, link-local)."""
    if not host:
        return True
    normalized = host.lower().strip()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_unspecified
    )


def _configured_proxies() -> tuple[str, str]:
    http_pref = ""
    https_pref = ""
    try:
        from sync_mcp.config import get_settings

        settings = get_settings()
        http_pref = (settings.http_proxy or "").strip()
        https_pref = (settings.https_proxy or "").strip()
    except Exception:  # noqa: BLE001
        pass
    http = http_pref or os.environ.get("HTTP_PROXY", "").strip()
    https = https_pref or os.environ.get("HTTPS_PROXY", "").strip() or http
    return http, https


def resolve_proxy_url(target_url: str) -> str | None:
    """Return proxy URL for public internet targets, or None for direct local/LAN access."""
    host = urlparse(target_url).hostname or ""
    if is_local_or_private_host(host):
        return None
    http_proxy, https_proxy = _configured_proxies()
    scheme = (urlparse(target_url).scheme or "http").lower()
    if scheme == "https":
        proxy = https_proxy
    else:
        proxy = http_proxy
    return proxy or None


def async_client_for(target_url: str, **kwargs: Any) -> httpx.AsyncClient:
    """httpx AsyncClient with selective proxy (trust_env=False for explicit routing)."""
    return httpx.AsyncClient(
        proxy=resolve_proxy_url(target_url),
        trust_env=False,
        **kwargs,
    )


def sync_client_for(target_url: str, **kwargs: Any) -> httpx.Client:
    """httpx Client with selective proxy (trust_env=False for explicit routing)."""
    return httpx.Client(
        proxy=resolve_proxy_url(target_url),
        trust_env=False,
        **kwargs,
    )
