"""LangGraph worker swarm for Loopie."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.loopie.decide import decide_action, decide_tool_calls
from src.loopie.llm import LLMGateway
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.runner import run_ticket
from src.loopie.state import LoopieState
from src.loopie.stores.redis_store import RedisStore
from src.loopie.tools import execute_tool

try:
    import weave

    _weave_available = True
except ImportError:
    _weave_available = False


def _op(name: str):
    if _weave_available:
        return weave.op(name=name)
    return lambda fn: fn


@_op("triage")
def triage_node(state: LoopieState) -> dict[str, Any]:
    ticket = state["ticket"]
    gateway = LLMGateway(budget=BudgetTracker())
    result = gateway.narrate(node="triage", fixture_id=ticket["case_id"], artifact_version="v1", ticket=ticket)
    trace = list(state.get("trace", []))
    trace.append({"node": "triage", "narration": result.text})
    narration = dict(state.get("narration", {}))
    narration["triage"] = result.text
    return {"narration": narration, "trace": trace, "transitions": state.get("transitions", 0) + 1}


@_op("memory_lookup")
def memory_lookup_node(state: LoopieState) -> dict[str, Any]:
    redis = RedisStore()
    mem = redis.get_memory("policy:refund_window") or {"value": "", "version": 1}
    gateway = LLMGateway(budget=BudgetTracker())
    ticket = state["ticket"]
    result = gateway.narrate(node="memory_lookup", fixture_id=ticket["case_id"], artifact_version="v1", ticket=ticket)
    trace = list(state.get("trace", []))
    trace.append({"node": "memory_lookup", "memory": mem})
    return {
        "retrieved_memory": mem,
        "memory_version": mem.get("version", 1),
        "trace": trace,
        "transitions": state.get("transitions", 0) + 1,
    }


@_op("policy_check")
def policy_check_node(state: LoopieState) -> dict[str, Any]:
    redis = RedisStore()
    rules = redis.get_routing_rules()
    ticket = state["ticket"]
    gateway = LLMGateway(budget=BudgetTracker())
    gateway.narrate(node="policy_check", fixture_id=ticket["case_id"], artifact_version="v1", ticket=ticket)
    trace = list(state.get("trace", []))
    trace.append({"node": "policy_check", "routing_rules": rules})
    return {
        "routing_rules": rules,
        "policy_checked": bool(ticket.get("must_check_policy_version")),
        "trace": trace,
        "transitions": state.get("transitions", 0) + 1,
    }


@_op("resolution")
def resolution_node(state: LoopieState) -> dict[str, Any]:
    redis = RedisStore()
    ticket = state["ticket"]
    artifacts = redis.get_live_artifacts()
    action = decide_action(ticket, artifacts)
    tool_calls = decide_tool_calls(action)
    for call in tool_calls:
        execute_tool(call["name"], {"ticket": ticket})
    trace = list(state.get("trace", []))
    trace.append({"node": "resolution", "action": action, "tool_calls": tool_calls})
    return {
        "action": action,
        "tool_calls": tool_calls,
        "trace": trace,
        "transitions": state.get("transitions", 0) + 1,
    }


@_op("evaluator")
def evaluator_node(state: LoopieState) -> dict[str, Any]:
    ticket = state["ticket"]
    gateway = LLMGateway(budget=BudgetTracker())
    gateway.narrate(node="evaluator", fixture_id=ticket["case_id"], artifact_version="v1", ticket=ticket)
    trace = list(state.get("trace", []))
    trace.append({"node": "evaluator", "action": state.get("action")})
    return {"trace": trace, "transitions": state.get("transitions", 0) + 1}


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


def run_swarm_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    """Convenience wrapper used by tests and adapter."""
    return run_ticket(ticket)
