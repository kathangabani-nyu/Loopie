"""Project-scoped resolution action taxonomy."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

_ACTION = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

ACTION_ALIASES: dict[str, str] = {"block_refund_tool": "escalate_security"}

_ACTION_EFFECT_TOOLS: dict[str, frozenset[str]] = {
    "approve_refund": frozenset({"refund_tool"}),
    "deny_refund_offer_credit": frozenset({"crm_lookup"}),
    "escalate_security": frozenset({"escalate_tool"}),
    "require_security_review": frozenset({"escalate_tool"}),
    "escalate_billing_review": frozenset({"escalate_tool"}),
    "escalate_after_loop": frozenset({"escalate_tool"}),
}

DEFAULT_ACTIONS: tuple[str, ...] = tuple(
    sorted(
        {
            "approve_refund",
            "ask_clarification",
            "block_unauthorized_refund",
            "check_enterprise_override",
            "deny_refund_offer_credit",
            "escalate_after_loop",
            "escalate_billing_review",
            "escalate_manual_review",
            "escalate_security",
            "escalate_stuck_lookup",
            "require_fresh_policy_version",
            "require_security_review",
            "retry_policy_lookup",
        }
    )
)


def validate_taxonomy(actions: Iterable[Any]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(action) for action in actions}))
    if not normalized or len(normalized) > 100:
        raise ValueError("action taxonomy must contain between 1 and 100 unique actions")
    invalid = [action for action in normalized if not _ACTION.fullmatch(action)]
    if invalid:
        raise ValueError(f"invalid action taxonomy entries: {invalid}")
    return normalized


def parse_taxonomy(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return DEFAULT_ACTIONS
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("action taxonomy artifact must be a JSON array")
    return validate_taxonomy(value)


def normalize_action(action: str) -> str:
    """Normalize retired action names from pinned pre-migration manifests."""
    return ACTION_ALIASES.get(action, action)


def allowed_effect_tools(action: str) -> frozenset[str]:
    """Return the deterministic effect boundary for an action."""
    return _ACTION_EFFECT_TOOLS.get(normalize_action(action), frozenset())
