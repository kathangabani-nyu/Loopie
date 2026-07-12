"""Compatibility exports for the golden oracle and tool mapping.

New production code imports ``taxonomy`` and ``reliability.oracle`` directly.
"""

from __future__ import annotations

from typing import Any

from src.loopie.reliability.oracle import decide_action, has_rule
from src.loopie.taxonomy import DEFAULT_ACTIONS

ALLOWED_ACTIONS: frozenset[str] = frozenset(DEFAULT_ACTIONS)
_has_rule = has_rule
__all__ = ["ALLOWED_ACTIONS", "decide_action", "decide_tool_calls"]


def decide_tool_calls(action: str) -> list[dict[str, Any]]:
    if action == "approve_refund":
        return [{"name": "refund_tool", "args": {}}]
    if action in {"escalate_security", "require_security_review"}:
        return [{"name": "escalate_tool", "args": {"reason": "security_flag"}}]
    if action == "deny_refund_offer_credit":
        return [{"name": "crm_lookup", "args": {}}]
    if action in {"escalate_billing_review", "require_security_review", "escalate_after_loop"}:
        return [{"name": "escalate_tool", "args": {}}]
    return []
