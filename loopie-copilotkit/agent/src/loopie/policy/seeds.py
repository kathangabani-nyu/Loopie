"""Seed policies that replace the demo's hardcoded policy semantics."""

from __future__ import annotations

from src.loopie.policy.dsl import PolicyRule, parse_policy_rule


SECURITY_FLAG_BLOCKS_REFUND: PolicyRule = parse_policy_rule(
    {
        "schema_version": "1",
        "rule_id": "security_flag_blocks_refund",
        "version": 1,
        "name": "Security-flagged accounts cannot invoke the refund tool",
        "status": "approved",
        "when": {
            "kind": "predicate",
            "path": "ticket.security_flag",
            "operator": "eq",
            "value": True,
        },
        "effects": [
            {
                "kind": "block",
                "path": "decision.tool_calls",
                "contains_any": ["refund_tool"],
                "message": "A security-flagged ticket attempted an unauthorized refund.",
            }
        ],
    }
)


REFUND_WINDOW_30_DAYS: PolicyRule = parse_policy_rule(
    {
        "schema_version": "1",
        "rule_id": "refund_window_30_days",
        "version": 1,
        "name": "Standard refunds after 30 days must be denied with credit",
        "status": "approved",
        "when": {
            "kind": "all",
            "conditions": [
                {
                    "kind": "predicate",
                    "path": "ticket.days_since_purchase",
                    "operator": "gt",
                    "value": 30,
                },
                {
                    "kind": "predicate",
                    "path": "ticket.customer_tier",
                    "operator": "neq",
                    "value": "enterprise",
                },
            ],
        },
        "effects": [
            {
                "kind": "require",
                "assertion": {
                    "kind": "predicate",
                    "path": "decision.action",
                    "operator": "eq",
                    "value": "deny_refund_offer_credit",
                },
                "message": "A standard refund outside the approved window was not denied.",
            }
        ],
    }
)


SEED_POLICIES = (SECURITY_FLAG_BLOCKS_REFUND, REFUND_WINDOW_30_DAYS)
