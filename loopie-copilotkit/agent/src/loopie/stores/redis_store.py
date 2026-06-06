"""Redis live substrate for Loopie artifacts and streams."""

from __future__ import annotations

import json
from typing import Any

import redis

from src.loopie.config import get_settings

PREFIX = "loopie:"


class RedisStore:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.Redis.from_url(url or get_settings().redis_url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def _key(self, category: str, name: str) -> str:
        return f"{PREFIX}{category}:{name}"

    def set_memory(self, key: str, value: str, version: int = 1) -> None:
        self._client.set(self._key("memory", key), json.dumps({"value": value, "version": version}))

    def get_memory(self, key: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key("memory", key))
        return json.loads(raw) if raw else None

    def set_routing_rules(self, rules: list[dict[str, Any]]) -> None:
        self._client.set(f"{PREFIX}routing:rules", json.dumps(rules))

    def get_routing_rules(self) -> list[dict[str, Any]]:
        raw = self._client.get(f"{PREFIX}routing:rules")
        return json.loads(raw) if raw else []

    def set_config(self, key: str, value: str | int) -> None:
        self._client.set(self._key("config", key), str(value))

    def get_config(self, key: str, default: str | None = None) -> str | None:
        return self._client.get(self._key("config", key)) or default

    def get_live_artifacts(self) -> dict[str, Any]:
        memory_raw = self.get_memory("policy:refund_window")
        memory = {}
        if memory_raw:
            memory["policy:refund_window"] = memory_raw.get("value", "")
        return {
            "memory": memory,
            "routing_rules": self.get_routing_rules(),
            "max_transitions": int(self.get_config("max_transitions", "6") or "6"),
        }

    def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        payload = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in fields.items()}
        return self._client.xadd(f"{PREFIX}events:{stream}", payload)

    def xread_recent(self, stream: str, count: int = 50) -> list[dict[str, Any]]:
        entries = self._client.xrevrange(f"{PREFIX}events:{stream}", count=count)
        events: list[dict[str, Any]] = []
        for entry_id, data in reversed(entries):
            events.append({"id": entry_id, **data})
        return events

    def flush_loopie_keys(self) -> None:
        for key in self._client.scan_iter(f"{PREFIX}*"):
            self._client.delete(key)
