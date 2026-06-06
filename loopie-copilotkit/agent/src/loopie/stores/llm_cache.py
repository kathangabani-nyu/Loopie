"""In-memory replay cache for live LLM completions."""

from __future__ import annotations

from typing import Any

from src.loopie.config import get_settings

_CACHE: dict[str, str] = {}


def cache_key(*, model: str, node: str, fixture_id: str, artifact_version: str) -> str:
    return f"{model}|{node}|{fixture_id}|{artifact_version}"


def get_cached(key: str) -> str | None:
    if not get_settings().enable_replay_cache:
        return None
    return _CACHE.get(key)


def set_cached(key: str, value: str) -> None:
    if get_settings().enable_replay_cache:
        _CACHE[key] = value


def clear_cache() -> None:
    _CACHE.clear()


def cache_stats() -> dict[str, Any]:
    return {"entries": len(_CACHE)}
