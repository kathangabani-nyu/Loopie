"""Full mock pipeline integration test."""

import os

import pytest

pytestmark = pytest.mark.integration

from src.loopie.config import get_settings


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_run_suite_mock_zero_cost(monkeypatch):
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.swarm import SWARM_NODE_ORDER

    from memory_stores import MemoryLedger, MemoryRedis

    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.preflight = {"ok": True, "provider_mode": "mock", "llm_mode": "mock"}
    pipeline.state = LoopiePipeline._initial_state()
    result = pipeline.run_suite(mode="mock")
    assert result["ok"] is True
    assert result["patched"]["passed"] is True
    assert result["counterfactual"]["no_regression"] is True
    assert pipeline.ledger.total_cost(mode="mock") == 0.0

    baseline_run = result["baseline"]["failure"]["run"]
    assert baseline_run["execution_engine"] == "langgraph_swarm"
    assert baseline_run["swarm_nodes"] == list(SWARM_NODE_ORDER)
    assert result["patched"]["run"]["execution_engine"] == "langgraph_swarm"
    assert pipeline.export_state()["preflight"]["provider_mode"] == "mock"


def test_hosted_mode_rejects_non_durable_stores(monkeypatch):
    """Hosted contract: memory ledger fallback is not audit-grade."""
    from src.loopie.preflight import assert_hosted_ready

    from memory_stores import MemoryLedger, MemoryRedis

    monkeypatch.setenv("LOOPIE_HOSTED", "1")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="Hosted Loopie preflight failed"):
        assert_hosted_ready(redis=MemoryRedis(), ledger=MemoryLedger())


def test_mock_run_records_oracle_decision(monkeypatch):
    """Mock mode always uses oracle — live differential lives in tests/test_live.py."""
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
    run = run_ticket(ticket, redis=pipeline.redis, ledger=pipeline.ledger, mode="mock")
    assert run["action"] == oracle
    assert run["decided_by"] == "oracle"
    assert run["fallback_used"] is False
