"""In-memory Redis/Ledger stubs for dependency-free fast-lane tests."""

from __future__ import annotations

import json
from typing import Any

from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


class MemoryRedis(RedisStore):
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._streams: dict[str, list] = {}

    def ping(self) -> bool:
        return True

    def set_memory(self, key: str, value: str, version: int = 1) -> None:
        self._data[f"memory:{key}"] = json.dumps({"value": value, "version": version})

    def get_memory(self, key: str) -> dict[str, Any] | None:
        raw = self._data.get(f"memory:{key}")
        return json.loads(raw) if raw else None

    def set_routing_rules(self, rules: list[dict[str, Any]]) -> None:
        self._data["routing:rules"] = json.dumps(rules)

    def get_routing_rules(self) -> list[dict[str, Any]]:
        raw = self._data.get("routing:rules")
        return json.loads(raw) if raw else []

    def set_config(self, key: str, value: str | int) -> None:
        self._data[f"config:{key}"] = str(value)

    def get_config(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(f"config:{key}", default)

    def get_live_artifacts(self) -> dict[str, Any]:
        memory_raw = self.get_memory("policy:refund_window")
        memory: dict[str, str] = {}
        if memory_raw:
            memory["policy:refund_window"] = memory_raw.get("value", "")
        return {
            "memory": memory,
            "routing_rules": self.get_routing_rules(),
            "max_transitions": int(self.get_config("max_transitions", "6") or "6"),
        }

    def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self._streams.setdefault(stream, []).append(fields)
        return "1-0"

    def xread_recent(self, stream: str, count: int = 50) -> list[dict[str, Any]]:
        return self._streams.get(stream, [])[-count:]

    def flush_loopie_keys(self) -> None:
        self._data.clear()
        self._streams.clear()

    def set_artifact_doc(self, artifact_key: str, doc: dict[str, Any]) -> bool:
        self._data[f"artifact:doc:{artifact_key}"] = json.dumps(doc)
        return True

    def patch_artifact_doc(self, artifact_key: str, path: str, value: Any) -> bool:
        raw = self._data.get(f"artifact:doc:{artifact_key}")
        doc = json.loads(raw) if raw else {}
        if path.startswith("$."):
            doc[path[2:]] = value
        self._data[f"artifact:doc:{artifact_key}"] = json.dumps(doc)
        return True

    def get_artifact_doc(self, artifact_key: str) -> dict[str, Any] | None:
        raw = self._data.get(f"artifact:doc:{artifact_key}")
        return json.loads(raw) if raw else None

    def preflight_capabilities(self) -> dict[str, Any]:
        return {"ping": True, "json": False, "search": False, "timeseries": False, "vector": False, "cluster_mode": False, "db": 0}


class MemoryLedger(Ledger):
    def __init__(self) -> None:
        super().__init__(url="postgresql://invalid", _memory_rows=[], _memory_costs=[])
        self._postgres_ok = False

    def ping(self) -> bool:
        self._postgres_ok = False
        return False

    def ensure_schema(self) -> None:
        return None

    def reset(self) -> None:
        self._memory_rows.clear()
        self._memory_costs.clear()

    def append_artifact_version(self, **kwargs: Any) -> None:
        row = {
            "artifact_key": kwargs["artifact_key"],
            "version": kwargs["version"],
            "value": kwargs["value"],
            "source_case": kwargs.get("source_case"),
            "correction_id": kwargs.get("correction_id"),
            "status": kwargs.get("status", "active"),
        }
        already = any(
            r["artifact_key"] == row["artifact_key"] and r["version"] == row["version"]
            for r in self._memory_rows
        )
        if not already:
            self._memory_rows.append(row)

    def record_cost(self, **kwargs: Any) -> None:
        self._memory_costs.append(kwargs)

    def total_cost(self, *, mode: str | None = None) -> float:
        rows = self._memory_costs if mode is None else [r for r in self._memory_costs if r.get("mode") == mode]
        return float(sum(r.get("estimated_cost", 0) for r in rows))

    def artifact_history(self, artifact_key: str) -> list[dict[str, Any]]:
        return [r for r in self._memory_rows if r["artifact_key"] == artifact_key]

    def record_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        return None
