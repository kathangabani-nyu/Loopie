"""Adapter stretch tests."""

from src.loopie.adapter import supervise_external_run
from src.loopie.decide import decide_action


def test_supervise_external_run_matches_oracle():
    ticket = {"case_id": "security_001", "security_flag": True, "request": "refund"}
    artifacts = {"routing_rules": [], "memory": {}}
    oracle = decide_action(ticket, artifacts)
    result = supervise_external_run(case_id="security_001", external_action=oracle)
    assert result["matches_oracle"] is True
