"""Redis live substrate for Loopie artifacts and streams."""

from __future__ import annotations

import json
from typing import Any

import redis

from src.loopie.config import get_settings

PREFIX = "loopie:"


class RedisStore:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.Redis.from_url(
            url or get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

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

    def preflight_capabilities(self) -> dict[str, Any]:
        """Probe Redis module/capability presence (not memory sizing)."""
        caps: dict[str, Any] = {
            "ping": False,
            "json": False,
            "search": False,
            "timeseries": False,
            "vector": False,
            "cluster_mode": False,
            "db": 0,
        }
        try:
            caps["ping"] = bool(self.ping())
            info = self._client.info("server")
            caps["cluster_mode"] = str(info.get("redis_mode", "")).lower() == "cluster"
        except Exception:
            return caps

        try:
            self._client.execute_command("JSON.SET", f"{PREFIX}preflight:json", "$", '{"ok":true}')
            self._client.delete(f"{PREFIX}preflight:json")
            caps["json"] = True
        except Exception:
            caps["json"] = False

        for module, flag in (("search", "search"), ("timeseries", "timeseries"), ("vector", "vector")):
            try:
                self._client.execute_command("MODULE", "LIST")
                modules = self._client.execute_command("MODULE", "LIST") or []
                text = str(modules).lower()
                caps[flag] = "search" in text or "ft" in text if flag == "search" else flag in text
            except Exception:
                caps[flag] = False

        return caps

    def set_artifact_doc(self, artifact_key: str, doc: dict[str, Any]) -> bool:
        """RedisJSON partial-friendly artifact doc when module is present."""
        key = f"{PREFIX}artifact:doc:{artifact_key}"
        try:
            self._client.execute_command("JSON.SET", key, "$", json.dumps(doc))
            return True
        except Exception:
            self._client.set(key, json.dumps(doc))
            return False

    def patch_artifact_doc(self, artifact_key: str, path: str, value: Any) -> bool:
        key = f"{PREFIX}artifact:doc:{artifact_key}"
        try:
            self._client.execute_command("JSON.SET", key, path, json.dumps(value))
            return True
        except Exception:
            raw = self._client.get(key)
            doc = json.loads(raw) if raw else {}
            if path.startswith("$."):
                doc[path[2:]] = value
            self._client.set(key, json.dumps(doc))
            return False

    def get_artifact_doc(self, artifact_key: str) -> dict[str, Any] | None:
        key = f"{PREFIX}artifact:doc:{artifact_key}"
        try:
            raw = self._client.execute_command("JSON.GET", key)
            return json.loads(raw) if raw else None
        except Exception:
            raw = self._client.get(key)
            return json.loads(raw) if raw else None
