"""LangGraph worker swarm for Loopie."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.loopie.config import get_settings
from src.loopie.decide import decide_action, decide_tool_calls, uses_live_decision
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION, LLMGateway
from src.loopie.run_context import get_run_context
from src.loopie.state import LoopieState
from src.loopie.tools import execute_tool

from src.loopie.observability import op as _op

SWARM_NODE_ORDER = ("triage", "memory_lookup", "policy_check", "resolution", "evaluator")


def _append_trace(state: LoopieState, entry: dict[str, Any]) -> list[dict[str, Any]]:
    trace = list(state.get("trace", []))
    trace.append(entry)
    return trace


def _maybe_stop_for_budget(state: LoopieState) -> bool:
    ctx = get_run_context()
    ctx.budget.record_transition()
    return ctx.budget.budget_guard_triggered


@_op("triage")
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
    return {
        "narration": narration,
        "trace": _append_trace(
            state,
            {
                "node": "triage",
                "narration": result.text,
                "mode": result.mode,
                "from_cache": result.from_cache,
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("memory_lookup")
def memory_lookup_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    mem = ctx.redis.get_memory("policy:refund_window") or {"value": "", "version": 1}
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
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("policy_check")
def policy_check_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered") or _maybe_stop_for_budget(state):
        return {"budget_guard_triggered": True, "transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    rules = ctx.redis.get_routing_rules()
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="policy_check",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=ctx.artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["policy_check"] = result.text
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
            },
        ),
        "transitions": ctx.budget.transitions,
    }


@_op("resolution")
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
        decision_schema_version = decision.decision_schema_version
        prompt_version = decision.prompt_version
        cache_hit = decision.from_cache
        narration["resolution"] = f"resolved:{action}"
        trace = _append_trace(
            state,
            {
                "node": "decision",
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

    tool_calls = decide_tool_calls(action)
    for call in tool_calls:
        execute_tool(call["name"], {"ticket": ticket, "action": action})

    return {
        "action": action,
        "tool_calls": tool_calls,
        "decided_by": decided_by,
        "fallback_used": fallback_used,
        "decision_schema_version": decision_schema_version,
        "prompt_version": prompt_version,
        "cache_hit": cache_hit,
        "narration": narration,
        "trace": trace,
        "transitions": ctx.budget.transitions,
    }


@_op("evaluator")
def evaluator_node(state: LoopieState) -> dict[str, Any]:
    if state.get("budget_guard_triggered"):
        return {"transitions": get_run_context().budget.transitions}

    ctx = get_run_context()
    ticket = state["ticket"]
    gateway = LLMGateway(budget=ctx.budget, ledger=ctx.ledger, eval_scope=ctx.eval_scope)
    result = gateway.narrate(
        node="evaluator",
        fixture_id=ticket["case_id"],
        artifact_version=ctx.artifact_version,
        ticket=ticket,
        artifacts=ctx.artifacts,
    )
    narration = dict(state.get("narration", {}))
    narration["evaluator"] = result.text
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
