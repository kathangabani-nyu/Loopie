"""Opt-in live LLM differential tests (hero + neighbors)."""

from __future__ import annotations

import os

import pytest

from src.loopie.config import get_settings
from src.loopie.decide import decide_action
from src.loopie.runner import run_ticket, seed_baseline, tickets_by_id
from src.loopie.stores.llm_cache import cache_key, clear_cache, get_cached, set_cached
from src.loopie.stores.redis_store import RedisStore


class MemoryRedis(RedisStore):
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._streams: dict[str, list] = {}

    def ping(self) -> bool:
        return True

    def set_memory(self, key, value, version=1):
        import json

        self._data[f"memory:{key}"] = json.dumps({"value": value, "version": version})

    def get_memory(self, key):
        import json

        raw = self._data.get(f"memory:{key}")
        return json.loads(raw) if raw else None

    def set_routing_rules(self, rules):
        import json

        self._data["routing:rules"] = json.dumps(rules)

    def get_routing_rules(self):
        import json

        raw = self._data.get("routing:rules")
        return json.loads(raw) if raw else []

    def set_policy_rules(self, rules):
        import json

        self._data["policy:rules"] = json.dumps(rules)

    def get_policy_rules(self):
        import json

        raw = self._data.get("policy:rules")
        return json.loads(raw) if raw else []

    def set_config(self, key, value):
        self._data[f"config:{key}"] = str(value)

    def get_config(self, key, default=None):
        return self._data.get(f"config:{key}", default)

    def get_live_artifacts(self):
        memory_raw = self.get_memory("policy:refund_window")
        memory = {}
        if memory_raw:
            memory["policy:refund_window"] = memory_raw.get("value", "")
        return {
            "memory": memory,
            "routing_rules": self.get_routing_rules(),
            "max_transitions": int(self.get_config("max_transitions", "6") or "6"),
        }

    def xadd(self, stream, fields):
        self._streams.setdefault(stream, []).append(fields)
        return "1-0"

    def xread_recent(self, stream, count=50):
        return self._streams.get(stream, [])[-count:]

    def get_llm_cache(self, cache_key):
        return self._data.get(f"llm-cache:{cache_key}")

    def set_llm_cache(self, cache_key, value, ttl_seconds=86_400):
        self._data[f"llm-cache:{cache_key}"] = value

    def flush_loopie_keys(self):
        self._data.clear()
        self._streams.clear()


@pytest.fixture
def memory_redis():
    return MemoryRedis()


live_opt_in = pytest.mark.skipif(
    os.getenv("LOOPIE_RUN_LIVE_TESTS") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="live opt-in: set LOOPIE_RUN_LIVE_TESTS=1 and OPENAI_API_KEY",
)


@pytest.mark.integration
@pytest.mark.live
@live_opt_in
def test_live_decision_equals_oracle_on_hero_and_neighbors(monkeypatch, memory_redis):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "live")
    monkeypatch.setenv("LOOPIE_LIVE_CONFIRMED", "1")
    monkeypatch.setenv("LOOPIE_ENABLE_REPLAY_CACHE", "true")
    get_settings.cache_clear()
    clear_cache()

    from src.loopie.stores.ledger import Ledger

    class MemoryLedger(Ledger):
        def __init__(self):
            super().__init__(url="postgresql://invalid", _memory_rows=[], _memory_costs=[])

        def ensure_schema(self):
            return None

    seed_baseline(redis=memory_redis, ledger=MemoryLedger())

    for case_id in sorted(tickets_by_id()):
        ticket = tickets_by_id()[case_id]
        artifacts = memory_redis.get_live_artifacts()
        oracle = decide_action(ticket, artifacts)
        run = run_ticket(ticket, redis=memory_redis, ledger=MemoryLedger(), mode="live")
        assert run["action"] == oracle, f"{case_id}: live {run['action']} != oracle {oracle}"
        assert run["decided_by"] == "llm", f"{case_id}: expected llm decision, got {run['decided_by']}"
        assert run["fallback_used"] is False, f"{case_id}: oracle fallback must fail the differential test"


@pytest.mark.integration
@pytest.mark.live
@live_opt_in
def test_replay_cache_hit_on_second_live_run(monkeypatch, memory_redis):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "live")
    monkeypatch.setenv("LOOPIE_LIVE_CONFIRMED", "1")
    monkeypatch.setenv("LOOPIE_ENABLE_REPLAY_CACHE", "true")
    get_settings.cache_clear()
    clear_cache()

    from src.loopie.stores.ledger import Ledger

    class MemoryLedger(Ledger):
        def __init__(self):
            super().__init__(url="postgresql://invalid", _memory_rows=[], _memory_costs=[])

        def ensure_schema(self):
            return None

    seed_baseline(redis=memory_redis, ledger=MemoryLedger())
    ticket = tickets_by_id()["security_001"]
    ledger = MemoryLedger()

    first = run_ticket(ticket, redis=memory_redis, ledger=ledger, mode="live")
    second = run_ticket(ticket, redis=memory_redis, ledger=ledger, mode="live")

    assert first["decided_by"] == "llm"
    assert first.get("stop_reason") == "completed"
    assert second.get("cache_hit") is True or any(
        step.get("from_cache") for step in second.get("trace", [])
    )


def test_cache_key_busts_on_artifact_hash_change():
    clear_cache()
    key_seed = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        artifact_hash="seed_hash",
    )
    key_patched = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        artifact_hash="patched_hash",
    )
    set_cached(key_seed, '{"action": "escalate_security"}')
    assert get_cached(key_seed) is not None
    assert get_cached(key_patched) is None


def test_cache_key_busts_on_prompt_version_change():
    clear_cache()
    key_v1 = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        prompt_version="v1",
        schema_version="v1",
    )
    key_v2 = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        prompt_version="v2",
        schema_version="v1",
    )
    set_cached(key_v1, '{"action": "escalate_security"}')
    assert get_cached(key_v1) is not None
    assert get_cached(key_v2) is None
