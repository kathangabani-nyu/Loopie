"""Adapter stretch tests."""

from src.loopie.adapter import supervise_external_run
from src.loopie.pipeline import LoopiePipeline

from memory_stores import MemoryLedger, MemoryRedis


def test_supervise_external_run_matches_oracle():
    pipeline = object.__new__(LoopiePipeline)
    pipeline.redis = MemoryRedis()
    pipeline.ledger = MemoryLedger()
    pipeline.state = LoopiePipeline._initial_state()
    pipeline.seed()

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
