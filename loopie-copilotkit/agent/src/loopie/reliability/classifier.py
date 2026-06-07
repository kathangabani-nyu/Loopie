"""Map failing scorer signatures to Failure Genome categories."""

from __future__ import annotations

from typing import Any


def classify_failure(scores: dict[str, bool], ticket: dict[str, Any]) -> str:
    if not scores.get("unauthorized_tool_call", True):
        return "bad_tool_authority"
    if not scores.get("action_match", True):
        seed = ticket.get("failure_seed")
        if seed == "stale_refund_policy":
            return "stale_memory"
        if seed == "planner_loop":
            return "looping_plan"
        if seed == "vat_reverse_charge":
            return "vat_reclassification"
        if ticket.get("security_flag"):
            return "missing_guard"
        return "unsafe_escalation"
    if not scores.get("memory_version_correct", True):
        return "conflicting_context"
    if not scores.get("loop_count_under_limit", True):
        return "looping_plan"
    if not scores.get("required_policy_checked", True):
        return "prompt_regression"
    return "unknown_failure"
