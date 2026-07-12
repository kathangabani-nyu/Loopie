"""Deterministic swarm tools with Policy DSL authorization receipts."""

from __future__ import annotations

from typing import Any

from src.loopie.artifacts import artifact_value_hash
from src.loopie.decide import _has_rule
from src.loopie.policy.dsl import evaluate_policy, parse_policy_rule

_SECURITY_GUARD = "security_flag_blocks_refund"


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


def refund_tool(context: dict[str, Any]) -> dict[str, Any]:
    ticket = context.get("ticket") or {}
    artifacts = context.get("artifacts") or {}
    action = context.get("action", "")
    amount = ticket.get("amount")
    policy_evidence = evaluate_decision_policies(ticket, artifacts, action, ["refund_tool"])
    if not policy_evidence["passed"]:
        return {
            "tool": "refund_tool",
            "authorization": "blocked",
            "reason": "; ".join(policy_evidence["violations"]),
            "amount": amount,
            "violated_rules": policy_evidence["violated_rules"],
        }
    if action == "approve_refund":
        return {
            "tool": "refund_tool",
            "authorization": "allowed",
            "reason": "all applicable approved policies passed",
            "amount": amount,
        }
    return {
        "tool": "refund_tool",
        "authorization": "blocked",
        "reason": f"action {action} blocks refund",
        "amount": amount,
    }


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


def audit_log_write(ledger: Any, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event_id = ledger.record_audit(event_type, payload)
    return {"tool": "audit_log_write", "audit_event_id": event_id, "event_type": event_type}


def run_evidence_tools(
    ticket: dict[str, Any],
    artifacts: dict[str, Any],
    action: str,
    policy_memory: dict[str, Any],
    ledger: Any,
) -> dict[str, Any]:
    crm = crm_lookup({"ticket": ticket})
    risk = risk_score_lookup({"ticket": ticket, "artifacts": artifacts})
    policy = policy_version_read(policy_memory)

    if action in {"escalate_security", "block_refund_tool", "require_security_review"}:
        resolution_tool = escalate_security({"ticket": ticket})
        tool_attempt = "escalate_security"
    elif action == "approve_refund":
        resolution_tool = refund_tool({"ticket": ticket, "artifacts": artifacts, "action": action})
        tool_attempt = "refund_tool"
    else:
        resolution_tool = escalate_security({"ticket": ticket})
        tool_attempt = "escalate_security"

    allowed = resolution_tool.get("authorization", "allowed") == "allowed"
    policy_result = "allowed" if allowed else "blocked"
    authorization = "allowed" if allowed else "denied_after_attempt"
    audit = audit_log_write(
        ledger,
        "swarm_resolution",
        {
            "case_id": ticket.get("case_id"),
            "action": action,
            "tool_attempt": tool_attempt,
            "policy_result": policy_result,
            "authorization": authorization,
            "violated_rules": resolution_tool.get("violated_rules", []),
        },
    )
    return {
        "crm": crm,
        "risk": risk,
        "policy": policy,
        "resolution_tool": resolution_tool,
        "tool_attempt": tool_attempt,
        "policy_result": policy_result,
        "authorization": authorization,
        "audit_event_id": audit.get("audit_event_id"),
    }


def execute_tool(name: str, context: dict[str, Any]) -> dict[str, Any]:
    if name == "refund_tool":
        return refund_tool(context)
    if name == "escalate_tool":
        return escalate_tool(context)
    if name == "crm_lookup":
        return crm_lookup(context)
    return {"tool": name, "status": "unknown"}
