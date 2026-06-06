"""Idempotent corrections + case-specific narration (#1 and #2)."""

import pytest

from src.loopie.config import get_settings
from src.loopie.pipeline import LoopiePipeline


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LOOPIE_LLM_MODE", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _approve_hero(pipeline: LoopiePipeline):
    pipeline.run_baseline(case_id="security_001")
    proposal = pipeline.propose_corrections()
    return proposal, pipeline.approve_correction(proposal["id"])


def test_reapproving_same_correction_is_a_noop():
    pipeline = LoopiePipeline()
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


def test_reset_returns_to_single_baseline_version():
    pipeline = LoopiePipeline()
    pipeline.reset()
    _approve_hero(pipeline)
    assert len(pipeline.get_artifact_history("routing:rules")) == 2  # v1 seed + v2 guard

    pipeline.reset()
    history = pipeline.get_artifact_history("routing:rules")
    assert len(history) == 1
    assert history[0]["version"] == 1


def test_narration_is_case_specific():
    pipeline = LoopiePipeline()
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
