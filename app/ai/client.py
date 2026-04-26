from __future__ import annotations

from functools import lru_cache
import os
from urllib.parse import urlparse

import anthropic
import httpx

from app.core.config import ANTHROPIC_API_KEY


DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_ANTHROPIC_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0)
_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_KNOWN_BAD_PROXY_HOST_PORTS = {"127.0.0.1:9", "localhost:9", "[::1]:9", "::1:9"}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _proxy_host_port(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = parsed.hostname or ""
    port = parsed.port
    if not host:
        raw = parsed.netloc or parsed.path
        if "@" in raw:
            raw = raw.rsplit("@", 1)[1]
        return raw.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}".lower() if port is not None else host.lower()


def _proxy_env_snapshot() -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for name in _PROXY_ENV_NAMES:
        value = os.getenv(name)
        snapshot[name] = {
            "present": bool(value),
            "host_port": _proxy_host_port(value),
        }
    return snapshot


def _invalid_proxy_reason(proxy_env: dict[str, dict[str, object]]) -> str:
    for name, item in proxy_env.items():
        host_port = str(item.get("host_port") or "").lower()
        if host_port in _KNOWN_BAD_PROXY_HOST_PORTS:
            return f"{name} points to invalid local proxy {host_port}"
    return ""


def get_anthropic_proxy_debug() -> dict[str, object]:
    proxy_env = _proxy_env_snapshot()
    proxy_detected = any(bool(item.get("present")) for item in proxy_env.values())
    proxy_invalid_reason = _invalid_proxy_reason(proxy_env)
    ignore_proxy_config = _env_flag("ANTHROPIC_IGNORE_PROXY")
    ignore_proxy_effective = ignore_proxy_config or bool(proxy_invalid_reason)
    return {
        "proxy_detected": proxy_detected,
        "proxy_used": proxy_detected and not ignore_proxy_effective,
        "proxy_invalid_reason": proxy_invalid_reason,
        "ignore_proxy_config": ignore_proxy_config,
        "ignore_proxy_effective": ignore_proxy_effective,
        "proxy_env": proxy_env,
    }


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    proxy_debug = get_anthropic_proxy_debug()
    if proxy_debug["ignore_proxy_effective"]:
        reason = proxy_debug["proxy_invalid_reason"] or "ANTHROPIC_IGNORE_PROXY=true"
        print(
            "[anthropic_client] "
            f"ignore_proxy=true proxy_detected={str(proxy_debug['proxy_detected']).lower()} "
            f"reason={reason}"
        )
        http_client = httpx.Client(trust_env=False, timeout=DEFAULT_ANTHROPIC_TIMEOUT)
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=http_client)
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
