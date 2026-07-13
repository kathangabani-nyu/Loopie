"""Bounded resolver agent inside Loopie's deterministic LangGraph control plane."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from src.loopie.llm import LLMGateway, deterministic_narration
from src.loopie.observability import op as _op
from src.loopie.run_context import get_run_context
from src.loopie.state import LoopieState
from src.loopie.taxonomy import normalize_action
from src.loopie.tools import authorize_and_execute, policy_version_read

SWARM_NODE_ORDER = ("triage", "context", "resolution", "execution", "evaluator")


def _append_trace(state: LoopieState, entry: dict[str, Any]) -> list[dict[str, Any]]:
    trace = list(state.get("trace", []))
    trace.append(entry)
    return trace


def _maybe_stop_for_budget(state: LoopieState) -> bool:
    ctx = get_run_context()
    ctx.budget.record_transition()
    return ctx.budget.budget_guard_triggered


def _timed(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(state: LoopieState) -> dict[str, Any]:
        start = time.perf_counter()
        result = fn(state)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        if result.get("trace"):
            trace = list(result["trace"])
            trace[-1] = {**trace[-1], "duration_ms": elapsed_ms}
            result = {**result, "trace": trace}
        return result

    return wrapper


@_op("triage")
@_timed
def triage_node(state: LoopieState) -> dict[str, Any]:
    if _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    receipt = {
        "classification": (
            "security_flagged_refund" if ticket.get("security_flag") else "standard_refund"
        ),
        "security_flag": bool(ticket.get("security_flag")),
        "amount": ticket.get("amount"),
        "amount_minor": ticket.get("amount_minor"),
        "currency": ticket.get("currency"),
        "amount_source": ticket.get("amount_source", "missing"),
        "tier": ticket.get("customer_tier", "standard"),
    }
    text = deterministic_narration("triage", ticket, receipt=receipt)
    narration = dict(state.get("narration", {}))
    narration["triage"] = text
    return {
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "triage",
                "narration": text,
                "mode": "deterministic",
                "from_cache": False,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("context")
@_timed
def context_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    memory = ctx.read_artifact("memory:policy:refund_window")
    routing_rules = ctx.read_artifact("routing:rules")
    policy_rules = ctx.read_artifact("policy:rules")
    memory_receipt = policy_version_read(memory)
    approved_rules = [rule for rule in policy_rules if rule.get("status") == "approved"]
    receipt = {
        "policy_version": memory_receipt["policy_version"],
        "freshness": memory_receipt["freshness"],
        "artifact_hash": memory_receipt["artifact_hash"],
        "approved_rules_loaded": len(approved_rules),
        "routing_rules_count": len(routing_rules),
        "security_guard_state": (
            "ACTIVE"
            if any(rule.get("rule") == "security_flag_blocks_refund" for rule in routing_rules)
            else "MISSING"
        ),
    }
    narration = dict(state.get("narration", {}))
    narration["memory_lookup"] = deterministic_narration(
        "memory_lookup", ticket, receipt=receipt
    )
    narration["policy_check"] = deterministic_narration(
        "policy_check", ticket, receipt=receipt
    )
    return {
        "retrieved_memory": memory,
        "memory_version": int(memory.get("version", 1)),
        "routing_rules": routing_rules,
        "policy_checked": True,
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "context",
                "narration": f"{narration['memory_lookup']} {narration['policy_check']}",
                "mode": "deterministic",
                "from_cache": False,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("resolution")
@_timed
def resolution_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    artifacts = ctx.artifacts()
    policy_memory = state.get("retrieved_memory") or ctx.read_artifact(
        "memory:policy:refund_window"
    )
    gateway = LLMGateway(
        budget=ctx.budget,
        ledger=ctx.ledger,
        eval_scope=ctx.eval_scope,
        cache_store=ctx.redis,
        cost_sink=ctx.cost_events,
        run_id=ctx.run_id,
    )
    episode = gateway.decide_episode(
        ticket,
        artifacts,
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        policy_memory=policy_memory,
        mode=ctx.mode,
    )
    action = normalize_action(episode.action)
    narration = dict(state.get("narration", {}))
    narration["resolution"] = f"resolution [{ticket['case_id']}]: {episode.reason}"
    receipt = {
        "iterations": episode.iterations,
        "evidence_calls": episode.evidence_calls,
        "proposed_tools": [item["name"] for item in episode.proposed_tools],
        "action": action,
    }
    return {
        "action": action,
        "proposed_tools": episode.proposed_tools,
        "evidence_calls": episode.evidence_calls,
        "decision_iterations": episode.iterations,
        "decided_by": episode.decided_by,
        "fallback_used": episode.fallback_used,
        "stop_reason": episode.stop_reason,
        "decision_schema_version": episode.decision_schema_version,
        "prompt_version": episode.prompt_version,
        "cache_hit": episode.from_cache,
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "resolution",
                "action": action,
                "decided_by": episode.decided_by,
                "fallback_used": episode.fallback_used,
                "artifact_basis": episode.artifact_basis,
                "reason": episode.reason,
                "from_cache": episode.from_cache,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("execution")
@_timed
def execution_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    action = normalize_action(str(state["action"]))
    evidence = authorize_and_execute(
        ticket,
        ctx.artifacts(),
        action,
        list(state.get("proposed_tools") or []),
    )
    policy_read_sets = {
        item["rule_id"]: item["read_set"] for item in evidence["policy"]["evaluations"]
    }
    receipt = {
        "proposed_tools": [item["name"] for item in evidence["proposed_tools"]],
        "policy_required_tools": [item["name"] for item in evidence["policy_required_tools"]],
        "authorized_tools": [item["name"] for item in evidence["authorized_tools"]],
        "blocked_tools": evidence["blocked_tools"],
        "denied_proposals": evidence["denied_proposals"],
        "prohibited_tools": evidence["prohibited_tools"],
        "executed_tools": [item["name"] for item in evidence["executed_tools"]],
        "policy_result": evidence["policy_result"],
        "policy_read_sets": policy_read_sets,
        "action": evidence["action"],
        "model_action": evidence["model_action"],
        "policy_enforced": evidence["policy_enforced"],
        "policy_overrode_action": evidence["policy_overrode_action"],
        "policy_enforced_by": evidence["policy_enforced_by"],
        "audit_event_id": None,
    }
    audit_payload = {
        **evidence["audit_payload"],
        "evidence_calls": list(state.get("evidence_calls") or []),
        "decision_iterations": int(state.get("decision_iterations", 0)),
        "decided_by": state.get("decided_by", "oracle"),
    }
    return {
        "action": evidence["action"],
        "model_action": evidence["model_action"],
        "policy_enforced": evidence["policy_enforced"],
        "policy_overrode_action": evidence["policy_overrode_action"],
        "policy_enforced_by": evidence["policy_enforced_by"],
        "tool_calls": evidence["proposed_tools"],
        "audit_event_id": None,
        "audit_payload": audit_payload,
        "tool_receipts": evidence["executed_tools"],
        "trace": _append_trace(
            state,
            {
                "node": "execution",
                "mode": "deterministic",
                "from_cache": False,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("evaluator")
@_timed
def evaluator_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered"):
        return {"transitions": get_run_context().budget.transitions}

    from src.loopie.reliability.scorers import score_run

    ctx = get_run_context()
    ticket = state["ticket"]
    artifacts = ctx.artifacts()
    partial_run = {
        "action": state.get("action"),
        "tool_calls": state.get("tool_calls", []),
        "transitions": ctx.budget.transitions,
        "policy_checked": bool(state.get("policy_checked")),
        "memory_version": int(state.get("memory_version", 1)),
        "max_transitions": int(artifacts.get("max_transitions", 6)),
        "decided_by": state.get("decided_by", "oracle"),
        "fallback_used": bool(state.get("fallback_used", False)),
    }
    scores = score_run(partial_run, ticket)
    receipt = {
        "scorers_passed": sum(1 for value in scores.values() if value),
        "scorers_total": len(scores),
        "audit_event_id": state.get("audit_event_id"),
    }
    text = deterministic_narration("evaluator", ticket, receipt=receipt)
    narration = dict(state.get("narration", {}))
    narration["evaluator"] = text
    return {
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "evaluator",
                "action": state.get("action"),
                "narration": text,
                "mode": "deterministic",
                "from_cache": False,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
        "execution_engine": "langgraph_bounded_agent",
    }


def build_graph():
    graph = StateGraph(LoopieState)
    graph.add_node("triage", triage_node)
    graph.add_node("context", context_node)
    graph.add_node("resolution", resolution_node)
    graph.add_node("execution", execution_node)
    graph.add_node("evaluator", evaluator_node)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "context")
    graph.add_edge("context", "resolution")
    graph.add_edge("resolution", "execution")
    graph.add_edge("execution", "evaluator")
    graph.add_edge("evaluator", END)
    return graph.compile()


graph = build_graph()


def run_swarm_ticket(ticket: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    from src.loopie.runner import run_ticket

    return run_ticket(ticket, **kwargs)
