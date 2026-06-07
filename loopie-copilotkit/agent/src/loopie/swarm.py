"""LangGraph worker swarm for Loopie."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from src.loopie.config import get_settings
from src.loopie.decide import decide_action, decide_tool_calls, uses_live_decision
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION, LLMGateway
from src.loopie.run_context import get_run_context
from src.loopie.state import LoopieState
from src.loopie.tools import policy_version_read, run_evidence_tools, execute_tool

from src.loopie.observability import op as _op

SWARM_NODE_ORDER = ("triage", "memory_lookup", "policy_check", "resolution", "evaluator")
_SECURITY_GUARD = "security_flag_blocks_refund"


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
        if "trace" in result and result["trace"]:
            trace = list(result["trace"])
            trace[-1] = {**trace[-1], "duration_ms": elapsed_ms}
            result = {**result, "trace": trace}
        return result

    return wrapper


def _has_guard(artifacts: dict[str, Any]) -> bool:
    return any(r.get("rule") == _SECURITY_GUARD for r in (artifacts.get("routing_rules") or []))


@_op("triage")
@_timed
def triage_node(state: LoopieState) -> dict[str, Any]:
    if _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="triage",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=ctx.artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["triage"] = result.text
    amount = ticket.get("amount")
    receipt = {
        "classification": "security_flagged_refund" if ticket.get("security_flag") else "standard_refund",
        "security_flag": bool(ticket.get("security_flag")),
        "amount": amount,
        "tier": ticket.get("customer_tier", "standard"),
    }
    return {
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "triage",
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("memory_lookup")
@_timed
def memory_lookup_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    mem = ctx.redis.get_memory("policy:refund_window") or {"value": "", "version": 1}
    policy_receipt = policy_version_read(ctx.redis)
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="memory_lookup",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=ctx.artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["memory_lookup"] = result.text
    receipt = {
        "policy_version": policy_receipt["policy_version"],
        "freshness": policy_receipt["freshness"],
        "artifact_hash": policy_receipt["artifact_hash"],
    }
    return {
        "retrieved_memory": mem,
        "memory_version": int(mem.get("version", 1)),
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "memory_lookup",
                "memory": mem,
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("policy_check")
@_timed
def policy_check_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    artifacts = ctx.artifacts or ctx.redis.get_live_artifacts()
    rules = ctx.redis.get_routing_rules()
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="policy_check",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["policy_check"] = result.text
    receipt = {
        "rule_checked": _SECURITY_GUARD,
        "present": _has_guard({"routing_rules": rules}),
    }
    return {
        "routing_rules": rules,
        "policy_checked": bool(ticket.get("must_check_policy_version")),
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "policy_check",
                "routing_rules": rules,
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
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
    settings = get_settings()
    ticket = state["ticket"]
    artifacts = ctx.artifacts or ctx.redis.get_live_artifacts()
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)

    decided_by = "oracle"
    fallback_used = False
    stop_reason = "mock"
    decision_schema_version = DECISION_SCHEMA_VERSION
    prompt_version = DECISION_PROMPT_VERSION
    cache_hit = False
    action: str

    narration = dict(state.get("narration", {}))

    if uses_live_decision(ticket, ctx.mode, settings):
        decision = gateway.decide(
            ticket,
            artifacts,
            fixture_id=ticket["case_id"],
            artifact_version=ctx.artifact_version,
        )
        action = decision.action
        decided_by = decision.decided_by
        fallback_used = decision.fallback_used
        stop_reason = decision.stop_reason
        decision_schema_version = decision.decision_schema_version
        prompt_version = decision.prompt_version
        cache_hit = decision.from_cache
        narration["resolution"] = f"resolved:{action}"
        trace = _append_trace(
            state,
            {
                "node": "resolution",
                "action": action,
                "decided_by": decided_by,
                "fallback_used": fallback_used,
                "artifact_basis": decision.artifact_basis,
                "reason": decision.reason,
                "from_cache": cache_hit,
            },
        )
    else:
        action = decide_action(ticket, artifacts)
        result = gateway.narrate(
            node="resolution",
            fixture_id=ticket["case_id"],
            artifact_version=ctx.artifact_version,
            ticket=ticket,
            artifacts=artifacts,
        )
        narration["resolution"] = result.text
        trace = _append_trace(
            state,
            {
                "node": "resolution",
                "action": action,
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
            },
        )

    evidence = run_evidence_tools(ticket, artifacts, action, ctx.redis, ctx.ledger)
    receipt = {
        "tool_attempt": evidence["tool_attempt"],
        "policy_result": evidence["policy_result"],
        "authorization": evidence["authorization"],
        "action": action,
        "audit_event_id": evidence.get("audit_event_id"),
    }
    trace = list(trace)
    trace[-1] = {**trace[-1], "receipt": receipt}

    tool_calls = decide_tool_calls(action)
    for call in tool_calls:
        execute_tool(call["name"], {"ticket": ticket, "action": action, "artifacts": artifacts})

    return {
        "action": action,
        "tool_calls": tool_calls,
        "decided_by": decided_by,
        "fallback_used": fallback_used,
        "stop_reason": stop_reason,
        "decision_schema_version": decision_schema_version,
        "prompt_version": prompt_version,
        "cache_hit": cache_hit,
        "narration": narration,
        "trace": trace,
        "audit_event_id": evidence.get("audit_event_id"),
        "transitions": ctx.budget.transitions,
    }


@_op("evaluator")
@_timed
def evaluator_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered"):
        return {"transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    artifacts = ctx.artifacts or ctx.redis.get_live_artifacts()
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="evaluator",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["evaluator"] = result.text

    from src.loopie.reliability.scorers import score_run

    partial_run = {
        "action": state.get("action"),
        "tool_calls": state.get("tool_calls", []),
        "transitions": ctx.budget.transitions,
        "policy_checked": bool(state.get("policy_checked", ticket.get("must_check_policy_version"))),
        "memory_version": int(state.get("memory_version", 1)),
        "max_transitions": int((artifacts or {}).get("max_transitions", 6)),
        "decided_by": state.get("decided_by", "oracle"),
        "fallback_used": bool(state.get("fallback_used", False)),
    }
    scores = score_run(partial_run, ticket)
    receipt = {
        "scorers_passed": sum(1 for v in scores.values() if v),
        "scorers_total": len(scores),
        "audit_event_id": state.get("audit_event_id"),
    }

    return {
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "evaluator",
                "action": state.get("action"),
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
                "receipt": receipt,
            },
        ),
        "transitions": ctx.budget.transitions,
        "execution_engine": "langgraph_swarm",
    }


def build_graph():
    graph = StateGraph(LoopieState)
    graph.add_node("triage", triage_node)
    graph.add_node("memory_lookup", memory_lookup_node)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("resolution", resolution_node)
    graph.add_node("evaluator", evaluator_node)

    graph.add_edge(START, "triage")
    graph.add_edge("triage", "memory_lookup")
    graph.add_edge("memory_lookup", "policy_check")
    graph.add_edge("policy_check", "resolution")
    graph.add_edge("resolution", "evaluator")
    graph.add_edge("evaluator", END)
    return graph.compile()


graph = build_graph()


def run_swarm_ticket(ticket: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper — executes through the LangGraph worker swarm."""
    from src.loopie.runner import run_ticket

    return run_ticket(ticket, **kwargs)
