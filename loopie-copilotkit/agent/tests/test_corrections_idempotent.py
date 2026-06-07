"""Idempotent corrections + case-specific narration (#1 and #2)."""

import pytest

from src.loopie.config import get_settings
from src.loopie.pipeline import LoopiePipeline
from memory_stores import MemoryLedger, MemoryRedis


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def pipeline() -> LoopiePipeline:
    p = object.__new__(LoopiePipeline)
    p.redis = MemoryRedis()
    p.ledger = MemoryLedger()
    p.state = LoopiePipeline._initial_state()
    return p


def _approve_hero(pipeline: LoopiePipeline):
    pipeline.run_baseline(case_id="security_001")
    proposal = pipeline.propose_corrections()
    return proposal, pipeline.approve_correction(proposal["id"])


def test_reapproving_same_correction_is_a_noop(pipeline: LoopiePipeline):
    pipeline.reset()

    proposal, first = _approve_hero(pipeline)
    assert first["no_op"] is False
    history_after_first = pipeline.get_artifact_history("routing:rules")
    version_after_first = first["version"]

    # Approving the identical correction again must NOT mint a new Time Machine version.
    second = pipeline.approve_correction(proposal["id"])
    assert second["no_op"] is True
    assert second["version"] == version_after_first

    history_after_second = pipeline.get_artifact_history("routing:rules")
    assert len(history_after_second) == len(history_after_first)


def test_reset_returns_to_single_baseline_version(pipeline: LoopiePipeline):
    pipeline.reset()
    _approve_hero(pipeline)
    assert len(pipeline.get_artifact_history("routing:rules")) == 2  # v1 seed + v2 guard

    pipeline.reset()
    history = pipeline.get_artifact_history("routing:rules")
    assert len(history) == 1
    assert history[0]["version"] == 1


def test_narration_is_case_specific(pipeline: LoopiePipeline):
    pipeline.reset()

    baseline = pipeline.run_baseline(case_id="security_001")
    narration = baseline["failure"]["run"]["narration"]
    # Baseline: the guard is absent -> the trace must say so (this is the "why").
    assert "MISSING" in narration["policy_check"]
    assert "security_001" in narration["triage"]

    proposal = pipeline.propose_corrections()
    pipeline.approve_correction(proposal["id"])
    pipeline.run_patched(case_id="security_001")
    # Patched run: the guard is now active.
    patched_run = next(r for r in pipeline.state["runs"].values() if r["label"] == "patched")
    assert "ACTIVE" in patched_run["run"]["narration"]["policy_check"]


def test_approve_surfaces_artifact_proof_payload(pipeline: LoopiePipeline):
    pipeline.reset()
    proposal, approved = _approve_hero(pipeline)

    assert approved["correction_id"] == proposal["id"]
    assert approved["before_hash"]
    assert approved["after_hash"]
    assert approved["before_hash"] != approved["after_hash"]
    assert isinstance(approved["diff"], list)
    assert len(approved["diff"]) > 0

    patched = pipeline.run_patched(case_id="security_001")
    proof = patched.get("artifact_proof")
    assert proof is not None
    assert proof["correction_id"] == proposal["id"]
    assert proof["before_hash"] == approved["before_hash"]
    assert proof["after_hash"] == approved["after_hash"]

    from src.loopie.reliability.evals import evaluate_suite

    eval_result = evaluate_suite(
        label="patched",
        redis=pipeline.redis,
        ledger=pipeline.ledger,
        correction_id=proposal["id"],
        artifact_proof=proof,
        limit=3,
    )
    columns = eval_result["proof_columns"]
    assert columns["before_hash"] == proof["before_hash"]
    assert columns["after_hash"] == proof["after_hash"]
    assert columns["diff"] == proof["diff"]
