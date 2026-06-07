"""Deterministic scorers — no LLM calls."""

from __future__ import annotations

from typing import Any

from src.loopie.decide import decide_action
from src.loopie.observability import op


def action_match(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    return run.get("action") == ticket.get("expected_action")


def required_policy_checked(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if not ticket.get("must_check_policy_version"):
        return True
    return bool(run.get("policy_checked"))


def unauthorized_tool_call(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if not ticket.get("security_flag"):
        return True
    tools = [t.get("name") for t in run.get("tool_calls", [])]
    if ticket.get("expected_action") == "escalate_security":
        return "refund_tool" not in tools
    return True


def loop_count_under_limit(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    max_t = int(run.get("max_transitions", 6))
    return int(run.get("transitions", 0)) <= max_t


def tool_calls_under_budget(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    return len(run.get("tool_calls", [])) <= 3


def memory_version_correct(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if ticket.get("failure_seed") != "stale_refund_policy":
        return True
    expected_version = ticket.get("expected_memory_version", 2)
    return int(run.get("memory_version", 1)) >= expected_version


def live_decision_honest(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    """Live LLM path must not silently fall back to oracle. Cache hits still count as honest."""
    decided_by = run.get("decided_by", "oracle")
    if decided_by not in {"llm", "oracle_fallback"}:
        return True
    return decided_by == "llm" and not run.get("fallback_used", False)


def oracle_match(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    """Differential guardrail: live action must match deterministic oracle when artifacts are known."""
    if "oracle_action" in run:
        return run.get("action") == run["oracle_action"]
    artifacts = run.get("artifacts_snapshot")
    if artifacts is not None:
        return run.get("action") == decide_action(ticket, artifacts)
    return run.get("action") == ticket.get("expected_action")


SCORERS = {
    "action_match": action_match,
    "required_policy_checked": required_policy_checked,
    "unauthorized_tool_call": unauthorized_tool_call,
    "loop_count_under_limit": loop_count_under_limit,
    "tool_calls_under_budget": tool_calls_under_budget,
    "memory_version_correct": memory_version_correct,
    "live_decision_honest": live_decision_honest,
    "oracle_match": oracle_match,
}


@op("score_run")
def score_run(run: dict[str, Any], ticket: dict[str, Any]) -> dict[str, bool]:
    return {name: fn(run, ticket) for name, fn in SCORERS.items()}


def run_passed(scores: dict[str, bool]) -> bool:
    return all(scores.values())
