"""Full mock pipeline integration test."""

import os

import pytest

from src.loopie.config import get_settings


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_run_suite_mock_zero_cost(monkeypatch):
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.stores.ledger import Ledger
    from src.loopie.stores.redis_store import RedisStore

    class MemoryRedis(RedisStore):
        def __init__(self):
            self._data: dict[str, str] = {}
            self._streams: dict[str, list] = {}

        def ping(self):
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

        def set_config(self, key, value):
            self._data[f"config:{key}"] = str(value)

        def get_config(self, key, default=None):
            return self._data.get(f"config:{key}", default)

        def xadd(self, stream, fields):
            self._streams.setdefault(stream, []).append(fields)
            return "1-0"

        def xread_recent(self, stream, count=50):
            return self._streams.get(stream, [])[-count:]

        def flush_loopie_keys(self):
            self._data.clear()
            self._streams.clear()

    class MemoryLedger(Ledger):
        def __init__(self):
            super().__init__(url="postgresql://invalid", _memory_rows=[], _memory_costs=[])

        def ensure_schema(self):
            return None

    pipeline = LoopiePipeline()
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    result = pipeline.run_suite(mode="mock")
    assert result["ok"] is True
    assert result["patched"]["passed"] is True
    assert result["counterfactual"]["no_regression"] is True
    assert pipeline.ledger.total_cost(mode="mock") == 0.0


def test_mock_run_records_oracle_decision(monkeypatch):
    """Mock mode always uses oracle — live differential lives in tests/test_live.py."""
    from src.loopie.decide import decide_action
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.runner import run_ticket, tickets_by_id
    from src.loopie.stores.redis_store import RedisStore

    class MemoryRedis(RedisStore):
        def __init__(self):
            self._data = {}
            self._streams = {}

        def ping(self):
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

        def set_config(self, key, value):
            self._data[f"config:{key}"] = str(value)

        def get_config(self, key, default=None):
            return self._data.get(f"config:{key}", default)

        def xadd(self, stream, fields):
            return "1-0"

        def xread_recent(self, stream, count=50):
            return []

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

    pipeline = LoopiePipeline()
    pipeline.redis = MemoryRedis()
    pipeline.seed()
    ticket = tickets_by_id()["security_001"]
    artifacts = pipeline.redis.get_live_artifacts()
    oracle = decide_action(ticket, artifacts)
    run = run_ticket(ticket, redis=pipeline.redis, ledger=pipeline.ledger, mode="mock")
    assert run["action"] == oracle
    assert run["decided_by"] == "oracle"
    assert run["fallback_used"] is False
