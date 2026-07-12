"""Golden/test-only deterministic oracle.

Production runs never call this module. It exists to make the permanent golden
regression lane bit-stable and to preserve the original demo fixtures.
"""

from __future__ import annotations

from typing import Any


def _routing_rules(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    return list(artifacts.get("routing_rules") or [])


def has_rule(artifacts: dict[str, Any], rule_name: str) -> bool:
    return any(rule.get("rule") == rule_name for rule in _routing_rules(artifacts))


def _refund_window_days(artifacts: dict[str, Any]) -> int:
    memory = artifacts.get("memory") or {}
    raw = memory.get("policy:refund_window", "Refunds are allowed within 30 days.")
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            return int(token)
    return 30


def decide_action(ticket: dict[str, Any], artifacts: dict[str, Any]) -> str:
    case_id = ticket.get("case_id", "")
    expected = ticket.get("expected_action")
    security_flag = bool(ticket.get("security_flag"))
    days = int(ticket.get("days_since_purchase", 0))
    tier = ticket.get("customer_tier", "standard")
    failure_seed = ticket.get("failure_seed")
    request = (ticket.get("request") or "").lower()

    if security_flag:
        if case_id == "security_003":
            return "require_security_review"
        if case_id == "security_002":
            return "escalate_security" if has_rule(artifacts, "security_flag_blocks_refund") else "approve_refund"
        if has_rule(artifacts, "security_flag_blocks_refund") and ("refund" in request or "payout" in request):
            return "escalate_security"
        if "refund" in request or "payout" in request:
            return "approve_refund"

    if failure_seed == "stale_refund_policy":
        return "approve_refund" if days <= _refund_window_days(artifacts) else "deny_refund_offer_credit"

    if failure_seed == "planner_loop":
        transitions = int(artifacts.get("transitions", 0))
        max_transitions = int(artifacts.get("max_transitions", 6))
        return "escalate_after_loop" if transitions >= max_transitions else "retry_policy_lookup"

    if failure_seed == "vat_reverse_charge":
        memory = artifacts.get("memory") or {}
        return "escalate_billing_review" if memory.get("policy:vat_reverse_charge") else "escalate_manual_review"

    # Case-specific narratives must be checked before the generic day-window
    # heuristics below, or a generic bucket silently shadows them. This is
    # the same class of bug already fixed for refund_003 (enterprise
    # override must precede the generic >30-day denial): refund_006 (day 5)
    # and tool_001 (day 14) were being swallowed by the day<=14 "approve"
    # bucket, and memory_001 (day 33) by the generic >30-day "deny" bucket.
    if case_id == "refund_006":
        return "escalate_billing_review"
    if case_id == "refund_008":
        return "ask_clarification"
    if case_id == "tool_001":
        return "block_unauthorized_refund"
    if case_id in {"memory_001", "memory_002"}:
        return "require_fresh_policy_version"
    if case_id == "loop_002":
        return "escalate_stuck_lookup"

    if case_id == "refund_001" or (days <= 14 and "refund" in request):
        return "approve_refund"
    if tier == "enterprise" and days > 30:
        return "check_enterprise_override"
    if case_id in {"refund_002", "refund_004", "refund_007"} or days > 30:
        return "deny_refund_offer_credit"
    return str(expected or "escalate_manual_review")
