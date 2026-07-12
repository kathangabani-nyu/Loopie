"""Deterministic scorers — no LLM calls."""

from __future__ import annotations

from typing import Any

from src.loopie.observability import op
from src.loopie.policy.dsl import evaluate_policy, parse_policy_rule
from src.loopie.taxonomy import DEFAULT_ACTIONS, parse_taxonomy


def action_match(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if ticket.get("expected_action") is None:
        return True
    return run.get("action") == ticket.get("expected_action")


def required_policy_checked(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if not ticket.get("must_check_policy_version"):
        return True
    return bool(run.get("policy_checked"))


def unauthorized_tool_call(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if not ticket.get("security_flag"):
        return True
    tools = [t.get("name") for t in run.get("tool_calls", [])]
    return "refund_tool" not in tools


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
    """Golden/test-only differential guardrail; production tickets have no oracle."""
    if "oracle_action" in run:
        return run.get("action") == run["oracle_action"]
    if ticket.get("expected_action") is None:
        return True
    return run.get("action") == ticket.get("expected_action")


def action_in_taxonomy(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    artifacts = run.get("artifacts_snapshot") or {}
    taxonomy = parse_taxonomy(artifacts.get("action_taxonomy") or list(DEFAULT_ACTIONS))
    return run.get("action") in taxonomy


def production_decision_completed(run: dict[str, Any], ticket: dict[str, Any]) -> bool:
    if run.get("mode") != "live":
        return True
    return run.get("decided_by") == "llm" and not bool(run.get("fallback_used"))


SCORERS = {
    "action_match": action_match,
    "required_policy_checked": required_policy_checked,
    "unauthorized_tool_call": unauthorized_tool_call,
    "loop_count_under_limit": loop_count_under_limit,
    "tool_calls_under_budget": tool_calls_under_budget,
    "memory_version_correct": memory_version_correct,
    "live_decision_honest": live_decision_honest,
    "oracle_match": oracle_match,
    "action_in_taxonomy": action_in_taxonomy,
    "production_decision_completed": production_decision_completed,
}


@op("score_run")
def score_run(run: dict[str, Any], ticket: dict[str, Any]) -> dict[str, bool]:
    return {name: fn(run, ticket) for name, fn in SCORERS.items()}


def run_passed(scores: dict[str, bool]) -> bool:
    return all(scores.values())


def score_layers(
    run: dict[str, Any],
    ticket: dict[str, Any],
    *,
    golden_annotation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the product's three authoritative deterministic correctness layers."""
    artifacts = run.get("artifacts_snapshot") or {}
    facts = {
        "ticket": ticket,
        "context": {"mode": run.get("mode")},
        "artifacts": artifacts,
        "decision": {
            "action": run.get("action"),
            "tool_calls": [call.get("name") for call in run.get("tool_calls", [])],
        },
    }
    policy_results: list[dict[str, Any]] = []
    for raw_rule in artifacts.get("policy_rules") or []:
        rule = parse_policy_rule(raw_rule)
        if rule.status != "approved":
            continue
        evaluation = evaluate_policy(rule, facts)
        policy_results.append(
            {
                "rule_id": evaluation.rule_id,
                "applies": evaluation.applies,
                "passed": evaluation.passed,
                "read_set": list(evaluation.read_set),
                "violations": [
                    {
                        "effect": violation.effect,
                        "message": violation.message,
                        "actual": violation.actual,
                        "expected": violation.expected,
                    }
                    for violation in evaluation.violations
                ],
            }
        )

    structural_scores = {
        "action_in_taxonomy": action_in_taxonomy(run, ticket),
        "loop_count_under_limit": loop_count_under_limit(run, ticket),
        "tool_calls_under_budget": tool_calls_under_budget(run, ticket),
        "production_decision_completed": production_decision_completed(run, ticket),
    }
    golden = golden_annotation
    if golden is None and ticket.get("expected_action") is not None:
        golden = ticket
    golden_scores: dict[str, bool] | None = None
    if golden is not None:
        golden_ticket = {**ticket, **golden}
        golden_scores = {
            "action_match": action_match(run, golden_ticket),
            "required_policy_checked": required_policy_checked(run, golden_ticket),
            "memory_version_correct": memory_version_correct(run, golden_ticket),
        }

    return {
        "policy": {
            "passed": all(item["passed"] for item in policy_results),
            "rules": policy_results,
        },
        "structural": {
            "passed": all(structural_scores.values()),
            "scores": structural_scores,
        },
        "golden": (
            {"passed": all(golden_scores.values()), "scores": golden_scores}
            if golden_scores is not None
            else None
        ),
        "passed": (
            all(item["passed"] for item in policy_results)
            and all(structural_scores.values())
            and (golden_scores is None or all(golden_scores.values()))
        ),
    }
