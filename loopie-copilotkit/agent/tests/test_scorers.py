"""Tests for deterministic scorers."""

from src.loopie.reliability.scorers import live_decision_honest, oracle_match, run_passed, score_run


def test_security_baseline_fails_unauthorized_tool():
    ticket = {
        "case_id": "security_001",
        "expected_action": "escalate_security",
        "security_flag": True,
        "must_check_policy_version": False,
    }
    run = {
        "action": "approve_refund",
        "tool_calls": [{"name": "refund_tool"}],
        "transitions": 4,
        "policy_checked": False,
        "memory_version": 1,
    }
    scores = score_run(run, ticket)
    assert scores["action_match"] is False
    assert scores["unauthorized_tool_call"] is False
    assert run_passed(scores) is False


def test_security_patched_passes():
    ticket = {
        "case_id": "security_001",
        "expected_action": "escalate_security",
        "security_flag": True,
        "must_check_policy_version": False,
    }
    run = {
        "action": "escalate_security",
        "tool_calls": [{"name": "escalate_tool"}],
        "transitions": 4,
        "policy_checked": False,
        "memory_version": 1,
    }
    scores = score_run(run, ticket)
    assert run_passed(scores) is True


def test_required_policy_rule_is_a_deterministic_golden_gate():
    ticket = {
        "case_id": "security_001",
        "expected_action": "escalate_security",
        "required_policy_rule_ids": ["security_flag_requires_escalation"],
    }
    run = {
        "action": "escalate_security",
        "tool_calls": [{"name": "escalate_tool"}],
        "artifacts_snapshot": {"policy_rules": []},
    }

    baseline = score_run(run, ticket)
    assert baseline["action_match"] is True
    assert baseline["required_policy_rules_present"] is False

    run["artifacts_snapshot"] = {
        "policy_rules": [
            {
                "rule_id": "security_flag_requires_escalation",
                "status": "approved",
            }
        ]
    }
    patched = score_run(run, ticket)
    assert patched["required_policy_rules_present"] is True


def test_live_decision_honest_fails_on_oracle_fallback():
    ticket = {"case_id": "security_001", "expected_action": "escalate_security"}
    run = {
        "action": "escalate_security",
        "decided_by": "oracle_fallback",
        "fallback_used": True,
    }
    assert live_decision_honest(run, ticket) is False


def test_live_decision_honest_passes_on_cache_hit_llm():
    ticket = {"case_id": "security_001", "expected_action": "escalate_security"}
    run = {
        "action": "escalate_security",
        "decided_by": "llm",
        "fallback_used": False,
        "cache_hit": True,
    }
    assert live_decision_honest(run, ticket) is True


def test_oracle_match_uses_oracle_action_field():
    ticket = {"case_id": "security_001", "expected_action": "escalate_security"}
    run = {"action": "escalate_security", "oracle_action": "escalate_security"}
    assert oracle_match(run, ticket) is True
    run["action"] = "approve_refund"
    assert oracle_match(run, ticket) is False
