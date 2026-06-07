"""Tests for Track #3 proof integrity (baseline eval, weave, fallback)."""

from __future__ import annotations

import pytest

from src.loopie.config import get_settings
from src.loopie.reliability.corrections import SECURITY_GUARD
from src.loopie.reliability.evals import evaluate_suite
from src.loopie.runner import seed_baseline
from src.loopie.stores.ledger import Ledger
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

    def flush_loopie_keys(self):
        self._data.clear()
        self._streams.clear()


@pytest.fixture(autouse=True)
def test_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class MemoryLedger(Ledger):
    def __init__(self):
        super().__init__(url="postgresql://invalid", _memory_rows=[], _memory_costs=[])

    def ensure_schema(self):
        return None


def test_baseline_eval_rewinds_artifacts_after_correction():
    """Baseline eval must read seed artifacts, not post-correction Redis state."""
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)

    rules = redis.get_routing_rules()
    rules.append(SECURITY_GUARD)
    redis.set_routing_rules(rules)
    assert any(r.get("rule") == SECURITY_GUARD["rule"] for r in redis.get_routing_rules())

    result = evaluate_suite(label="baseline", redis=redis, ledger=ledger, limit=10)
    assert result["artifacts_rewound"] is True
    assert not any(r.get("rule") == SECURITY_GUARD["rule"] for r in redis.get_routing_rules())

    sec2 = next(r for r in result["results"] if r["case_id"] == "security_002")
    assert sec2["action"] == "approve_refund"


def test_cache_key_busts_on_artifact_hash_change():
    clear_cache()
    key_a = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        artifact_hash="hash_seed",
    )
    key_b = cache_key(
        model="gpt-4o-mini",
        node="decision",
        fixture_id="security_001",
        artifact_version="v1",
        artifact_hash="hash_patched",
    )
    set_cached(key_a, '{"action": "escalate_security"}')
    assert get_cached(key_a) is not None
    assert get_cached(key_b) is None


def test_weave_eval_error_is_surfaced_not_silent(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    monkeypatch.setenv("LOOPIE_WEAVE_ENABLED", "true")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    get_settings.cache_clear()

    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)

    def _boom(_coro):
        raise RuntimeError("weave evaluation unavailable")

    monkeypatch.setattr("src.loopie.reliability.evals.asyncio.run", _boom)

    result = evaluate_suite(label="baseline", redis=redis, ledger=ledger, limit=3, mode="test")
    assert result["weave_eval_error"] is not None
    assert "weave evaluation unavailable" in result["weave_eval_error"]
    assert result["weave_eval_id"] is None
    assert result["results"] == []


def test_weave_tracing_enabled_in_test_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    monkeypatch.setenv("LOOPIE_WEAVE_ENABLED", "true")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    get_settings.cache_clear()

    from src.loopie.observability import weave_tracing_enabled

    assert weave_tracing_enabled() is True


def test_weave_tracing_requires_explicit_flag(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    monkeypatch.delenv("LOOPIE_WEAVE_ENABLED", raising=False)
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    get_settings.cache_clear()

    from src.loopie.observability import weave_tracing_enabled

    assert weave_tracing_enabled() is False


@pytest.mark.integration
def test_run_suite_live_fails_when_whitelist_case_used_fallback(monkeypatch):
    from src.loopie.decide import decide_action
    from src.loopie.llm import LLMDecisionResult, LLMGateway, LLMResult
    from src.loopie.pipeline import LoopiePipeline

    monkeypatch.setenv("LOOPIE_LLM_MODE", "live")
    monkeypatch.setenv("LOOPIE_LIVE_CONFIRMED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()

    def _stub_narrate(self, *, node, fixture_id, artifact_version, ticket=None, artifacts=None):
        return LLMResult(
            text=f"live {node}",
            mode="live",
            model="gpt-4o-mini",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            stop_reason="test",
        )

    monkeypatch.setattr(LLMGateway, "narrate", _stub_narrate)

    def _skip_eval(**_kwargs):
        return {
            "label": _kwargs.get("label"),
            "results": [],
            "weave_eval_error": None,
            "weave_eval_id": None,
        }

    monkeypatch.setattr("src.loopie.reliability.evals.evaluate_suite", _skip_eval)

    original_decide = LLMGateway.decide

    def _fallback_decide(self, ticket, artifacts, *, fixture_id, artifact_version):
        oracle = decide_action(ticket, artifacts)
        if ticket.get("case_id") in {"security_001", "refund_001", "security_002", "security_003"}:
            return LLMDecisionResult(
                action=oracle,
                mode="live",
                model="gpt-4o-mini",
                decided_by="oracle_fallback",
                fallback_used=True,
                security_guard_observed=False,
                artifact_basis=[],
                reason="forced fallback for test",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
                stop_reason="test",
            )
        return original_decide(self, ticket, artifacts, fixture_id=fixture_id, artifact_version=artifact_version)

    monkeypatch.setattr(LLMGateway, "decide", _fallback_decide)

    pipeline = LoopiePipeline()
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()

    result = pipeline.run_suite(mode="live", reset=True)
    assert result["live_fallback_cases"]
    assert result["ok"] is False
