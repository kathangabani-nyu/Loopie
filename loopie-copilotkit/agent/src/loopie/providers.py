"""OpenAI-compatible provider registry with role routing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AGENT_ROOT = Path(__file__).resolve().parents[2]
CURSOR_SMOKE_MARKER = _AGENT_ROOT / ".cursor_smoke_ok"


def cursor_smoke_verified() -> bool:
    """True when smoke test passed in this shell or via persisted marker file."""
    if os.getenv("LOOPIE_CURSOR_SMOKE_OK") == "1":
        return True
    try:
        return CURSOR_SMOKE_MARKER.is_file()
    except OSError:
        return False


def write_cursor_smoke_marker() -> Path:
    """Persist smoke-test success for subsequent shells (non-secret)."""
    CURSOR_SMOKE_MARKER.write_text("ok\n", encoding="utf-8")
    return CURSOR_SMOKE_MARKER


def is_gpt5_model(model: str) -> bool:
    return model.lower().startswith("gpt-5")


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str | None
    api_key: str | None
    model: str
    enabled: bool


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(f"LOOPIE_PROVIDER_{name.upper()}_ENABLED")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def provider_registry() -> dict[str, ProviderConfig]:
    openai_key = os.getenv("OPENAI_API_KEY")
    cursor_key = os.getenv("CURSOR_API_KEY") or os.getenv("LOOPIE_CURSOR_API_KEY")
    cursor_verified = cursor_smoke_verified()

    return {
        "openai": ProviderConfig(
            name="openai",
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=openai_key,
            model=os.getenv("LOOPIE_OPENAI_MODEL", "gpt-4o-mini"),
            enabled=_enabled("openai", bool(openai_key)),
        ),
        "cursor": ProviderConfig(
            name="cursor",
            base_url=os.getenv("LOOPIE_CURSOR_BASE_URL", "https://api.cursor.com/v1"),
            api_key=cursor_key,
            model=os.getenv("LOOPIE_CURSOR_MODEL", "gpt-4o-mini"),
            enabled=_enabled("cursor", False) and cursor_verified and bool(cursor_key),
        ),
    }


def role_provider_chain(role: str) -> list[str]:
    """Ordered failover chain per role (decision/narration/supervisory)."""
    override = os.getenv(f"LOOPIE_{role.upper()}_PROVIDER")
    if override:
        return [p.strip() for p in override.split(",") if p.strip()]
    default = os.getenv("LOOPIE_PROVIDER_CHAIN", "openai,cursor")
    return [p.strip() for p in default.split(",") if p.strip()]


def resolve_provider(role: str, registry: dict[str, ProviderConfig] | None = None) -> ProviderConfig | None:
    registry = registry or provider_registry()
    for name in role_provider_chain(role):
        cfg = registry.get(name)
        if cfg and cfg.enabled and cfg.api_key:
            return cfg
    return None


def openai_client_kwargs(cfg: ProviderConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "api_key": cfg.api_key,
    }
    if not is_gpt5_model(cfg.model):
        kwargs["temperature"] = 0
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return kwargs
