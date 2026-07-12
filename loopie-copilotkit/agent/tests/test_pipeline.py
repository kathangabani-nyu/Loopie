"""Full test pipeline integration test."""

import pytest

from src.loopie.config import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def test_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_run_suite_test_zero_cost(monkeypatch):
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.swarm import SWARM_NODE_ORDER

    from memory_stores import MemoryLedger, MemoryRedis

    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.preflight = {"ok": True, "provider_mode": "test", "llm_mode": "test"}
    pipeline.state = LoopiePipeline._initial_state()
    result = pipeline.run_suite(mode="test")
    assert result["ok"] is True
    assert result["patched"]["passed"] is True
    assert result["counterfactual"]["no_regression"] is True
    assert pipeline.ledger.total_cost(mode="test") == 0.0

    baseline_run = result["baseline"]["failure"]["run"]
    assert baseline_run["execution_engine"] == "langgraph_swarm"
    assert baseline_run["swarm_nodes"] == list(SWARM_NODE_ORDER)
    assert result["patched"]["run"]["execution_engine"] == "langgraph_swarm"
    assert pipeline.export_state()["preflight"]["provider_mode"] == "test"


def test_hosted_mode_rejects_non_durable_stores(monkeypatch):
    """Hosted contract: memory ledger fallback is not audit-grade."""
    from src.loopie.preflight import assert_hosted_ready

    from memory_stores import MemoryLedger, MemoryRedis

    monkeypatch.setenv("LOOPIE_HOSTED", "1")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="Hosted Loopie preflight failed"):
        assert_hosted_ready(redis=MemoryRedis(), ledger=MemoryLedger())


def test_test_run_records_oracle_decision(monkeypatch):
    """Test mode always uses oracle — live differential lives in tests/test_live.py."""
    from src.loopie.decide import decide_action
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.runner import run_ticket, tickets_by_id

    from memory_stores import MemoryLedger, MemoryRedis

    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.state = LoopiePipeline._initial_state()
    pipeline.seed()
    ticket = tickets_by_id()["security_001"]
    artifacts = pipeline.redis.get_live_artifacts()
    oracle = decide_action(ticket, artifacts)
    run = run_ticket(ticket, redis=pipeline.redis, ledger=pipeline.ledger, mode="test")
    assert run["action"] == oracle
    assert run["decided_by"] == "oracle"
    assert run["fallback_used"] is False


def test_pipeline_records_operation_timings_and_export_budget(monkeypatch):
    from src.loopie.pipeline import LoopiePipeline

    from memory_stores import MemoryLedger, MemoryRedis

    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.state = LoopiePipeline._initial_state()
    pipeline.seed()
    pipeline.run_baseline(case_id="security_001")

    exported = pipeline.export_state()
    timings = exported.get("operationTimings") or []
    assert any(entry.get("action") == "baseline" for entry in timings)
    assert all(entry.get("elapsed_ms", 0) >= 0 for entry in timings)

    budget = exported.get("budget") or {}
    assert budget.get("actual_model_cost_usd") == 0.0
    assert budget.get("estimated_run_cost_usd", 0) > 0
    assert budget.get("wall_clock_s", 0) > 0
    assert budget.get("estimate_basis") == "wall_clock_ms + trace nodes + eval cases"
