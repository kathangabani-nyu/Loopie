"""Tests for Track #3 proof integrity (baseline eval, weave, fallback)."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from memory_stores import MemoryLedger, MemoryRedis
from src.loopie.config import get_settings
from src.loopie.reliability.corrections import SECURITY_GUARD
from src.loopie.reliability.evals import evaluate_suite
from src.loopie.runner import seed_baseline
from src.loopie.stores.llm_cache import cache_key, clear_cache, get_cached, set_cached


@pytest.fixture(autouse=True)
def test_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    monkeypatch.setattr("src.loopie.reliability.evals.ensure_weave", lambda: None)

    class StubEvaluateOp:
        async def call(self, _predictor):
            return {}, None

    class StubEvaluation:
        def __init__(self, **_kwargs):
            self.evaluate = StubEvaluateOp()

    monkeypatch.setattr("weave.Evaluation", StubEvaluation)
    monkeypatch.setattr("weave.attributes", lambda _attrs: nullcontext())

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


def test_weave_eval_uses_authoritative_call_url(monkeypatch):
    monkeypatch.setenv("LOOPIE_WEAVE_ENABLED", "true")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    monkeypatch.setenv("WANDB_ENTITY", "loopie-team")
    get_settings.cache_clear()
    monkeypatch.setattr("src.loopie.reliability.evals.ensure_weave", lambda: True)
    monkeypatch.setattr("src.loopie.reliability.evals._build_weave_scorers", lambda: [])

    def fake_predictor(ctx):
        def predict(ticket, artifact_version):
            run = {
                "case_id": ticket["case_id"],
                "action": ticket.get("expected_action"),
                "oracle_action": ticket.get("expected_action"),
                "mode": "test",
                "tool_calls": [],
                "transitions": 1,
                "max_transitions": 6,
                "policy_checked": True,
                "memory_version": ticket.get("expected_memory_version", 2),
                "artifacts_snapshot": {},
            }
            ctx["runs_by_case"][ticket["case_id"]] = run
            return run

        return predict

    monkeypatch.setattr(
        "src.loopie.reliability.evals._build_weave_predictor", fake_predictor
    )

    class StubEvaluateOp:
        def __init__(self, evaluation):
            self.evaluation = evaluation

        async def call(self, predictor):
            for row in self.evaluation.dataset:
                predictor(
                    ticket=row["ticket"], artifact_version=row["artifact_version"]
                )
            return {}, SimpleNamespace(
                id="call-123",
                ui_url="https://wandb.ai/loopie-team/loopie/weave/calls/call-123",
            )

    class StubEvaluation:
        def __init__(self, **kwargs):
            self.dataset = kwargs["dataset"]
            self.evaluate = StubEvaluateOp(self)

    monkeypatch.setattr("weave.Evaluation", StubEvaluation)
    monkeypatch.setattr("weave.attributes", lambda _attrs: nullcontext())

    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    result = evaluate_suite(
        label="baseline", redis=redis, ledger=ledger, limit=2, mode="test"
    )

    assert result["weave_eval_error"] is None
    assert result["weave_eval_id"] == "call-123"
    assert result["weave_project_url"] == (
        "https://wandb.ai/loopie-team/loopie/weave/calls/call-123"
    )


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


def test_failed_weave_init_is_not_retried_for_every_operation(monkeypatch):
    monkeypatch.setenv("LOOPIE_WEAVE_ENABLED", "true")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    monkeypatch.setenv("WANDB_ENTITY", "missing-team")
    get_settings.cache_clear()

    import src.loopie.observability as observability

    calls = []

    class BrokenWeave:
        @staticmethod
        def init(project):
            calls.append(project)
            raise RuntimeError("project unavailable")

    monkeypatch.setattr(observability, "_weave", BrokenWeave())
    monkeypatch.setattr(observability, "_weave_available", True)
    monkeypatch.setattr(observability, "_weave_initialized", False)
    monkeypatch.setattr(observability, "_weave_retry_after", 0.0)
    monkeypatch.setattr(observability, "_weave_init_error", None)

    assert observability.ensure_weave() is False
    assert observability.ensure_weave() is False
    assert calls == ["missing-team/loopie"]
    status = observability.weave_runtime_status()
    assert status["ready"] is False
    assert "project unavailable" in status["error"]


def test_live_honesty_gate_fails_when_any_case_used_oracle_fallback():
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.reliability.scorers import live_decision_honest

    fallback_run = {
        "case_id": "security_001",
        "mode": "live",
        "decided_by": "oracle_fallback",
        "fallback_used": True,
    }
    honest_run = {
        "case_id": "refund_001",
        "mode": "live",
        "decided_by": "llm",
        "fallback_used": False,
    }
    tickets = {
        "security_001": {"case_id": "security_001"},
        "refund_001": {"case_id": "refund_001"},
    }

    assert LoopiePipeline._collect_live_fallback_cases(fallback_run, honest_run) == [
        "security_001"
    ]
    assert LoopiePipeline._collect_dishonest_live_cases(
        fallback_run,
        honest_run,
        tickets=tickets,
    ) == ["security_001"]
    assert live_decision_honest(fallback_run, tickets["security_001"]) is False
