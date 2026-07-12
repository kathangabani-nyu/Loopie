"""Project-scoped resolution action taxonomy."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

_ACTION = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

DEFAULT_ACTIONS: tuple[str, ...] = tuple(
    sorted(
        {
            "approve_refund",
            "ask_clarification",
            "block_refund_tool",
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
