"""Exhaustive semantics for the deterministic Policy DSL v1 surface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.loopie.policy.compiler import validate_compiled_policy
from src.loopie.policy.dsl import evaluate_policy, parse_policy_rule
from src.loopie.policy.seeds import REFUND_WINDOW_30_DAYS, SECURITY_FLAG_BLOCKS_REFUND


def _facts(*, security: bool = False, days: int = 10, tier: str = "standard", action: str, tools=()):
    return {
        "ticket": {
            "security_flag": security,
            "days_since_purchase": days,
            "customer_tier": tier,
        },
        "context": {},
        "artifacts": {},
        "decision": {"action": action, "tool_calls": list(tools)},
    }


def test_security_rule_detects_demo_baseline_and_accepts_patch():
    baseline = evaluate_policy(
        SECURITY_FLAG_BLOCKS_REFUND,
        _facts(security=True, action="approve_refund", tools=("refund_tool",)),
    )
    patched = evaluate_policy(
        SECURITY_FLAG_BLOCKS_REFUND,
        _facts(security=True, action="escalate_security"),
    )

    assert baseline.applies is True
    assert baseline.passed is False
    assert baseline.violations[0].effect == "block"
    assert patched.applies is True
    assert patched.passed is True
    assert baseline.read_set == ("decision.tool_calls", "ticket.security_flag")


def test_rule_does_not_apply_without_security_flag():
    result = evaluate_policy(
        SECURITY_FLAG_BLOCKS_REFUND,
        _facts(action="approve_refund", tools=("refund_tool",)),
    )
    assert result.applies is False
    assert result.passed is True


@pytest.mark.parametrize(
    ("days", "tier", "action", "applies", "passed"),
    [
        (12, "standard", "approve_refund", False, True),
        (31, "standard", "approve_refund", True, False),
        (31, "standard", "deny_refund_offer_credit", True, True),
        (38, "enterprise", "check_enterprise_override", False, True),
    ],
)
def test_refund_window_semantics(days, tier, action, applies, passed):
    result = evaluate_policy(
        REFUND_WINDOW_30_DAYS,
        _facts(days=days, tier=tier, action=action),
    )
    assert result.applies is applies
    assert result.passed is passed


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "matched"),
    [
        ("eq", "a", "a", True),
        ("neq", "a", "b", True),
        ("gt", 3, 2, True),
        ("gte", 3, 3, True),
        ("lt", 2, 3, True),
        ("lte", 3, 3, True),
        ("in", "billing", ["billing", "refund"], True),
        ("contains", "Refund requested", "refund", True),
        ("exists", "present", True, True),
        ("exists", None, False, True),
    ],
)
def test_predicate_operator_matrix(operator, actual, expected, matched):
    rule = parse_policy_rule(
        {
            "rule_id": f"test_{operator}",
            "version": 1,
            "name": f"Test {operator} semantics",
            "status": "approved",
            "when": {
                "kind": "predicate",
                "path": "ticket.value",
                "operator": operator,
                "value": expected,
            },
            "effects": [
                {
                    "kind": "require",
                    "assertion": {
                        "kind": "predicate",
                        "path": "decision.valid",
                        "operator": "eq",
                        "value": True,
                    },
                    "message": "Decision must be valid.",
                }
            ],
        }
    )
    result = evaluate_policy(rule, {"ticket": {"value": actual}, "decision": {"valid": True}})
    assert result.applies is matched


def test_unknown_fields_and_unsafe_paths_are_rejected():
    payload = {
        "rule_id": "unsafe_rule",
        "version": 1,
        "name": "Unsafe generated rule",
        "when": {"kind": "predicate", "path": "secrets.api_key", "operator": "exists", "value": True},
        "effects": [
            {
                "kind": "escalate_to",
                "action": "escalate_security",
                "message": "Escalate.",
            }
        ],
        "invented": "field",
    }
    with pytest.raises(ValidationError):
        parse_policy_rule(payload)


def test_compiled_policy_is_forced_to_proposed_status():
    compiled = validate_compiled_policy(
        {
            "rule": {
                "rule_id": "compiled_security_rule",
                "version": 7,
                "name": "Compiled security escalation",
                "status": "approved",
                "when": {
                    "kind": "predicate",
                    "path": "ticket.security_flag",
                    "operator": "eq",
                    "value": True,
                },
                "effects": [
                    {
                        "kind": "escalate_to",
                        "action": "escalate_security",
                        "message": "Escalate security-sensitive requests.",
                    }
                ],
            },
            "rationale": "The source policy requires a security escalation.",
        },
        action_taxonomy=["escalate_security"],
    )

    assert compiled.rule.status == "proposed"


def test_compiled_policy_rejects_action_outside_project_taxonomy():
    with pytest.raises(ValueError, match="outside the project taxonomy"):
        validate_compiled_policy(
            {
                "rule": {
                    "rule_id": "compiled_unknown_action",
                    "version": 1,
                    "name": "Unknown action rule",
                    "when": {
                        "kind": "predicate",
                        "path": "ticket.security_flag",
                        "operator": "eq",
                        "value": True,
                    },
                    "effects": [
                        {
                            "kind": "escalate_to",
                            "action": "send_money_now",
                            "message": "Use an action not registered by this project.",
                        }
                    ],
                },
                "rationale": "The source text asks for an unsupported action.",
            },
            action_taxonomy=["escalate_security"],
        )
