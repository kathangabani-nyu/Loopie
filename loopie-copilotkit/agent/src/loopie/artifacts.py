"""Artifact hashing and Redis snapshot helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.loopie.stores.redis_store import RedisStore

_DATA_DIR = Path(__file__).resolve().parent / "data"


def artifact_content_hash(artifacts: dict[str, Any]) -> str:
    """Stable short hash of live artifact contents for replay-cache keys."""
    payload = json.dumps(artifacts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def artifact_value_hash(value: Any) -> str:
    """Stable short hash of a single artifact document value."""
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def artifact_structured_diff(before: Any, after: Any) -> list[dict[str, Any]]:
    """Concrete before/after diff for proof payloads and cockpit display."""
    if before == after:
        return []

    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        keys = sorted(set(before) | set(after))
        for key in keys:
            b_val = before.get(key)
            a_val = after.get(key)
            if b_val == a_val:
                continue
            if isinstance(b_val, dict) and isinstance(a_val, dict):
                nested = artifact_structured_diff(b_val, a_val)
                if nested:
                    changes.append({"path": key, "changes": nested})
            elif isinstance(b_val, list) and isinstance(a_val, list):
                changes.append({"path": key, "before": b_val, "after": a_val})
            else:
                changes.append({"path": key, "before": b_val, "after": a_val})
        return changes

    return [{"path": ".", "before": before, "after": after}]


def build_artifact_proof(
    *,
    correction_id: str | None,
    before_value: Any | None,
    after_value: Any,
) -> dict[str, Any]:
    """Proof bundle consumed by cockpit, Weave leaderboards, and eval payloads."""
    return {
        "correction_id": correction_id,
        "before_hash": artifact_value_hash(before_value) if before_value is not None else None,
        "after_hash": artifact_value_hash(after_value),
        "diff": artifact_structured_diff(before_value, after_value) if before_value is not None else [],
    }


def snapshot_redis_artifacts(redis: RedisStore) -> dict[str, Any]:
    """Capture Redis artifact state for later restore."""
    return {
        "policy_refund_window": redis.get_memory("policy:refund_window"),
        "routing_rules": redis.get_routing_rules(),
        "max_transitions": redis.get_config("max_transitions", "6"),
    }


def restore_redis_artifacts(redis: RedisStore, snapshot: dict[str, Any]) -> None:
    """Restore Redis memory, routing rules, and config from a snapshot."""
    mem = snapshot.get("policy_refund_window")
    if mem:
        redis.set_memory(
            "policy:refund_window",
            mem.get("value", ""),
            version=int(mem.get("version", 1)),
        )
    redis.set_routing_rules(list(snapshot.get("routing_rules") or []))
    redis.set_config("max_transitions", snapshot.get("max_transitions", "6"))


def apply_seed_artifacts_to_redis(redis: RedisStore) -> dict[str, Any]:
    """Rewind Redis to seeded baseline artifacts (no ledger writes)."""
    seed_memory = json.loads((_DATA_DIR / "seed_memory.json").read_text(encoding="utf-8"))
    seed_rules = json.loads((_DATA_DIR / "seed_routing_rules.json").read_text(encoding="utf-8"))

    mem = seed_memory["memory"]
    redis.set_memory(mem["key"], mem["value"], version=mem["version"])
    redis.set_routing_rules(seed_rules.get("rules", []))
    redis.set_config("max_transitions", seed_memory.get("max_transitions", 6))
    return redis.get_live_artifacts()
