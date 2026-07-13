"""Deterministic swarm tools with Policy DSL authorization receipts."""

from __future__ import annotations

from typing import Any

from src.loopie.artifacts import artifact_value_hash
from src.loopie.decide import _has_rule
from src.loopie.observability import op
from src.loopie.policy.dsl import EscalateEffect, evaluate_policy, parse_policy_rule
from src.loopie.taxonomy import allowed_effect_tools, normalize_action, parse_taxonomy

_SECURITY_GUARD = "security_flag_blocks_refund"


@op("tool.crm_lookup", kind="tool")
def crm_lookup(context: dict[str, Any]) -> dict[str, Any]:
    ticket = context.get("ticket") or {}
    tier = ticket.get("customer_tier", "standard")
    ltv_map = {"enterprise": 125_000, "standard": 4_200, "trial": 180}
    return {
        "tool": "crm_lookup",
        "customer_tier": tier,
        "lifetime_value_usd": ltv_map.get(tier, 4_200),
        "account_id": f"acct_{ticket.get('case_id', 'unknown')}",
    }


@op("tool.risk_score_lookup", kind="tool")
def risk_score_lookup(context: dict[str, Any]) -> dict[str, Any]:
    ticket = context.get("ticket") or {}
    artifacts = context.get("artifacts") or {}
    score = 0.15
    reasons: list[str] = []
    if ticket.get("security_flag"):
        score += 0.55
        reasons.append("active_security_flag")
    if ticket.get("security_flag") and not _has_rule(artifacts, _SECURITY_GUARD):
        score += 0.2
        reasons.append("missing_payout_guard")
    amount = float(ticket.get("amount", 0) or 0)
    if amount >= 5_000:
        score += 0.1
        reasons.append("high_value_transaction")
    return {
        "tool": "risk_score_lookup",
        "risk_score": round(min(score, 0.99), 2),
        "reasons": reasons,
    }


@op("tool.policy_version_read", kind="tool")
def policy_version_read(mem: dict[str, Any] | None, key: str = "policy:refund_window") -> dict[str, Any]:
    mem = mem or {"value": "", "version": 1}
    version = int(mem.get("version", 1))
    content = mem.get("value", "")
    return {
        "tool": "policy_version_read",
        "policy_version": version,
        "freshness": "stale" if version < 2 else "fresh",
        "artifact_hash": artifact_value_hash({"value": content, "version": version}),
        "key": key,
    }


def evaluate_decision_policies(
    ticket: dict[str, Any],
    artifacts: dict[str, Any],
    action: str,
    tool_names: list[str],
) -> dict[str, Any]:
    """Authorize a proposed decision with the exact pinned Policy DSL bundle."""
    violated_rules: list[str] = []
    violations: list[str] = []
    evaluations: list[dict[str, Any]] = []
    facts = {
        "ticket": ticket,
        "context": {},
        "artifacts": artifacts,
        "decision": {"action": action, "tool_calls": tool_names},
    }
    for raw_rule in artifacts.get("policy_rules") or []:
        rule = parse_policy_rule(raw_rule)
        if rule.status != "approved":
            continue
        result = evaluate_policy(rule, facts)
        evaluations.append(
            {
                "rule_id": result.rule_id,
                "applies": result.applies,
                "passed": result.passed,
                "read_set": list(result.read_set),
            }
        )
        if not result.passed:
            violated_rules.append(result.rule_id)
            violations.extend(violation.message for violation in result.violations)
    return {
        "passed": not violated_rules,
        "violated_rules": violated_rules,
        "violations": violations,
        "evaluations": evaluations,
    }


def enforce_decision_action(
    ticket: dict[str, Any],
    artifacts: dict[str, Any],
    model_action: str,
) -> dict[str, Any]:
    """Apply mandatory approved ``escalate_to`` effects before tool authorization.

    The model still authors the proposal, but an approved Policy DSL artifact is
    the deterministic authority for mandatory escalation. Conflicting policies
    fail closed instead of choosing an arbitrary action.
    """

    model_action = normalize_action(model_action)
    facts = {
        "ticket": ticket,
        "context": {},
        "artifacts": artifacts,
        "decision": {"action": model_action, "tool_calls": []},
    }
    targets: dict[str, list[str]] = {}
    for raw_rule in artifacts.get("policy_rules") or []:
        rule = parse_policy_rule(raw_rule)
        if rule.status != "approved":
            continue
        evaluation = evaluate_policy(rule, facts)
        if not evaluation.applies:
            continue
        for effect in rule.effects:
            if isinstance(effect, EscalateEffect):
                target = normalize_action(effect.action)
                targets.setdefault(target, []).append(rule.rule_id)

    if len(targets) > 1:
        raise ValueError(
            "conflicting mandatory escalation policies: "
            + ", ".join(sorted(targets))
        )
    action = next(iter(targets), model_action)
    if action not in parse_taxonomy(artifacts.get("action_taxonomy")):
        raise ValueError(f"policy-enforced action is outside the pinned taxonomy: {action}")
    rule_ids = targets.get(action, []) if targets else []
    return {
        "action": action,
        "model_action": model_action,
        "policy_enforced": bool(rule_ids),
        "policy_overrode_action": bool(rule_ids) and action != model_action,
        "policy_enforced_by": rule_ids,
    }


@op("tool.refund_tool", kind="tool")
def refund_tool(context: dict[str, Any]) -> dict[str, Any]:
    ticket = context.get("ticket") or {}
    amount = ticket.get("amount")
    return {
        "tool": "refund_tool",
        "status": "simulated",
        "amount": amount,
        "amount_minor": ticket.get("amount_minor"),
        "currency": ticket.get("currency"),
        "case_id": ticket.get("case_id"),
    }


@op("tool.escalate_security", kind="tool")
def escalate_security(context: dict[str, Any]) -> dict[str, Any]:
    ticket = context.get("ticket") or {}
    return {
        "tool": "escalate_security",
        "ticket_id": ticket.get("case_id"),
        "queue": "security-ops-tier2",
        "priority": "high" if ticket.get("security_flag") else "normal",
    }


def escalate_tool(context: dict[str, Any]) -> dict[str, Any]:
    return escalate_security(context)


def execute_evidence_tool(
    name: str,
    *,
    ticket: dict[str, Any],
    artifacts: dict[str, Any],
    policy_memory: dict[str, Any],
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a read-only evidence tool against pinned run inputs."""
    if args:
        raise ValueError(f"evidence tool {name} does not accept arguments")
    if name == "crm_lookup":
        return crm_lookup({"ticket": ticket})
    if name == "risk_score_lookup":
        return risk_score_lookup({"ticket": ticket, "artifacts": artifacts})
    if name == "policy_version_read":
        return policy_version_read(policy_memory)
    raise ValueError(f"unknown evidence tool: {name}")


def authorize_and_execute(
    ticket: dict[str, Any],
    artifacts: dict[str, Any],
    action: str,
    proposed_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Authorize model-proposed effects once, then execute only approved calls."""
    resolution = enforce_decision_action(ticket, artifacts, action)
    action = str(resolution["action"])
    proposed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in proposed_tools:
        name = str(raw.get("name", ""))
        if not name or name in seen:
            continue
        proposed.append({"name": name, "args": dict(raw.get("args") or {})})
        seen.add(name)

    proposed_names = [item["name"] for item in proposed]
    allowed_for_action = allowed_effect_tools(action)
    denied_names = {name for name in proposed_names if name not in allowed_for_action}
    prohibited_names = {"refund_tool"} if ticket.get("security_flag") else set()
    denied_names.update(name for name in proposed_names if name in prohibited_names)

    eligible = [item for item in proposed if item["name"] not in denied_names]
    policy_required = [
        {"name": name, "args": {}}
        for name in sorted(allowed_for_action)
        if resolution["policy_enforced"] and name not in {item["name"] for item in eligible}
    ]
    candidates = [*eligible, *policy_required]
    policy = evaluate_decision_policies(
        ticket,
        artifacts,
        action,
        [item["name"] for item in candidates],
    )
    if not policy["passed"]:
        denied_names.update(item["name"] for item in candidates)
    authorized = [item for item in candidates if item["name"] not in denied_names]
    executed = []
    for call in authorized:
        result = execute_tool(
            call["name"],
            {"ticket": ticket, "action": action, "artifacts": artifacts, "args": call["args"]},
        )
        executed.append({"name": call["name"], "mode": "simulated", "receipt": result})

    blocked_names = denied_names | prohibited_names
    audit_payload = {
        "case_id": ticket.get("case_id"),
        "action": action,
        "model_action": resolution["model_action"],
        "policy_enforced": resolution["policy_enforced"],
        "policy_overrode_action": resolution["policy_overrode_action"],
        "policy_enforced_by": resolution["policy_enforced_by"],
        "proposed_tools": proposed_names,
        "policy_required_tools": [item["name"] for item in policy_required],
        "authorized_tools": [item["name"] for item in authorized],
        "blocked_tools": sorted(blocked_names),
        "denied_proposals": sorted(denied_names),
        "prohibited_tools": sorted(prohibited_names),
        "executed_tools": [item["name"] for item in executed],
        "policy_result": "allowed" if policy["passed"] and not denied_names else "blocked",
        "violated_rules": policy["violated_rules"],
    }
    return {
        **resolution,
        "policy": policy,
        "proposed_tools": proposed,
        "policy_required_tools": policy_required,
        "authorized_tools": authorized,
        "blocked_tools": sorted(blocked_names),
        "denied_proposals": sorted(denied_names),
        "prohibited_tools": sorted(prohibited_names),
        "executed_tools": executed,
        "policy_result": audit_payload["policy_result"],
        "audit_payload": audit_payload,
    }


def execute_tool(name: str, context: dict[str, Any]) -> dict[str, Any]:
    if name == "refund_tool":
        return refund_tool(context)
    if name == "escalate_tool":
        return escalate_tool(context)
    if name == "crm_lookup":
        return crm_lookup(context)
    if name == "risk_score_lookup":
        return risk_score_lookup(context)
    if name == "policy_version_read":
        return policy_version_read(context.get("policy_memory"))
    raise ValueError(f"unknown tool: {name}")
