"""Extended failure mode coverage."""

import os

import pytest

from src.loopie.config import get_settings


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_stale_memory_correction_path():
    from src.loopie.decide import decide_action
    from src.loopie.pipeline import LoopiePipeline
    from src.loopie.reliability.classifier import classify_failure
    from src.loopie.reliability.corrections import apply, propose
    from src.loopie.reliability.scorers import run_passed, score_run
    from src.loopie.runner import run_ticket, tickets_by_id

    from memory_stores import MemoryLedger, MemoryRedis

    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.state = LoopiePipeline._initial_state()
    pipeline.seed()
    ticket = tickets_by_id()["refund_007"]
    baseline = run_ticket(ticket, redis=pipeline.redis, ledger=pipeline.ledger)
    scores = score_run(baseline, ticket)
    assert run_passed(scores) is False
    category = classify_failure(scores, ticket)
    correction = propose(category, case_id="refund_007")
    apply(correction, redis=pipeline.redis, ledger=pipeline.ledger)
    patched = run_ticket(ticket, redis=pipeline.redis, ledger=pipeline.ledger, artifact_version="v2")
    assert run_passed(score_run(patched, ticket)) is True
    artifacts = pipeline.redis.get_live_artifacts()
    assert decide_action(ticket, artifacts) == "deny_refund_offer_credit"
