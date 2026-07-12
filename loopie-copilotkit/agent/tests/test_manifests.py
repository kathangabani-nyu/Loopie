from __future__ import annotations

from typing import Any

from src.loopie.manifests import ManifestReader, build_run_manifest
from src.loopie.runner import run_ticket

from .memory_stores import MemoryLedger, MemoryRedis


def _seed(redis: MemoryRedis) -> None:
    redis.set_memory("policy:refund_window", "Refunds allowed within 30 days.", version=1)
    redis.set_routing_rules([{"rule": "route_enterprise", "target": "tier2"}])
    redis.set_policy_rules([])
    redis.set_config("max_transitions", 6)


def _ticket() -> dict[str, Any]:
    return {
        "case_id": "manifest-1",
        "days_since_purchase": 5,
        "amount": 20,
        "customer_tier": "standard",
        "expected_action": "approve_refund",
        "expected_tool_calls": ["refund_tool"],
    }


def _manifest(redis: MemoryRedis):
    return build_run_manifest(
        redis,
        _ticket(),
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        model_version="test-model",
    )


def test_manifest_is_unchanged_after_redis_and_caller_mutation() -> None:
    redis = MemoryRedis()
    _seed(redis)
    reader = ManifestReader(_manifest(redis))

    first = reader.legacy_artifacts()
    redis.set_memory("policy:refund_window", "Changed after run start.", version=99)
    redis.set_routing_rules([{"rule": "changed"}])
    first["routing_rules"].append({"rule": "caller_mutation"})

    second = reader.legacy_artifacts()
    assert second["memory"]["policy:refund_window"] == "Refunds allowed within 30 days."
    assert second["routing_rules"] == [{"rule": "route_enterprise", "target": "tier2"}]


def test_read_set_hashes_are_anchored_to_manifest() -> None:
    redis = MemoryRedis()
    _seed(redis)
    manifest = _manifest(redis)
    reader = ManifestReader(manifest)
    reader.legacy_artifacts()

    expected = {item.key: (item.version, item.content_hash) for item in manifest.artifacts}
    actual = {item["key"]: (item["version"], item["content_hash"]) for item in reader.read_set()}
    assert actual == expected


class CountingRedis(MemoryRedis):
    def __init__(self) -> None:
        super().__init__()
        self.reads = {"memory": {}, "routing": 0, "policy": 0, "config": {}}

    def get_memory(self, key: str) -> dict[str, Any] | None:
        reads = self.reads["memory"]
        reads[key] = reads.get(key, 0) + 1
        if reads[key] > 1:
            raise AssertionError(f"in-flight run reread Redis memory {key}")
        return super().get_memory(key)

    def get_routing_rules(self) -> list[dict[str, Any]]:
        self.reads["routing"] += 1
        if self.reads["routing"] > 1:
            raise AssertionError("in-flight run reread Redis routing rules")
        return super().get_routing_rules()

    def get_policy_rules(self) -> list[dict[str, Any]]:
        self.reads["policy"] += 1
        if self.reads["policy"] > 1:
            raise AssertionError("in-flight run reread Redis policy rules")
        return super().get_policy_rules()

    def get_config(self, key: str, default: str | None = None) -> str | None:
        reads = self.reads["config"]
        reads[key] = reads.get(key, 0) + 1
        if reads[key] > 1:
            raise AssertionError(f"in-flight run reread Redis config {key}")
        return super().get_config(key, default)


def test_run_samples_redis_once_and_emits_complete_manifest_read_set() -> None:
    redis = CountingRedis()
    _seed(redis)
    run = run_ticket(_ticket(), redis=redis, ledger=MemoryLedger(), mode="test")

    assert redis.reads == {
        "memory": {"policy:refund_window": 1, "policy:vat_reverse_charge": 1},
        "routing": 1,
        "policy": 1,
        "config": {"max_transitions": 1, "action_taxonomy": 1},
    }
    manifest_items = {item["key"]: item for item in run["run_manifest"]["artifacts"]}
    reads = {item["key"]: item for item in run["read_set"]}
    assert reads.keys() == manifest_items.keys()
    for key, read in reads.items():
        assert read["version"] == manifest_items[key]["version"]
        assert read["content_hash"] == manifest_items[key]["content_hash"]
