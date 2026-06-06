"""Adapter stretch tests."""

from src.loopie.adapter import supervise_external_run
from src.loopie.pipeline import LoopiePipeline


def test_supervise_external_run_matches_oracle():
    # Reset to a clean baseline so the live Redis substrate has no security guard;
    # otherwise leftover artifacts from prior demo runs make this non-deterministic.
    pipeline = LoopiePipeline()
    pipeline.reset()

    # In the un-patched baseline the oracle (wrongly) allows the refund — that is the
    # very failure Loopie later corrects.
    result = supervise_external_run(
        case_id="security_001", external_action="approve_refund", pipeline=pipeline
    )
    assert result["oracle_action"] == "approve_refund"
    assert result["matches_oracle"] is True

    # And a divergent external action is detected as a mismatch.
    mismatch = supervise_external_run(
        case_id="security_001", external_action="escalate_security", pipeline=pipeline
    )
    assert mismatch["matches_oracle"] is False
