"""CopilotKit control agent for the Loopie cockpit."""

from __future__ import annotations

import os
from typing import Any

import httpx
from copilotkit import CopilotKitMiddleware, StateStreamingMiddleware, StateItem
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.types import Command
from typing_extensions import TypedDict

from src.loopie.chat_cost import (
    ChatBudgetExceeded,
    LedgerCostCallback,
    budget_degraded_message,
    handle_chat_budget_error,
    max_chat_cost_usd,
)
from src.loopie.providers import is_gpt5_model, openai_client_kwargs, provider_registry, resolve_provider
from src.loopie.stores.ledger import Ledger

API_BASE = os.getenv("LOOPIE_API_BASE", "http://localhost:8001").rstrip("/")
_HTTP_TIMEOUT = 120.0


class LoopieControlState(TypedDict, total=False):
    runs: dict[str, Any]
    currentFailure: dict[str, Any] | None
    proposedCorrections: list[dict[str, Any]]
    artifactHistory: list[dict[str, Any]]
    artifactProof: dict[str, Any] | None
    evalDelta: dict[str, Any]
    counterfactual: dict[str, Any]
    weaveEvalBaseline: dict[str, Any]
    weaveEvalPatched: dict[str, Any]
    events: list[dict[str, Any]]
    budget: dict[str, Any]
    operationTimings: list[dict[str, Any]]
    approvalState: str
    preflight: dict[str, Any]


def chat_api_key_configured() -> bool:
    cfg = resolve_provider("supervisory") or provider_registry()["openai"]
    return bool(cfg.api_key and str(cfg.api_key).strip())


def _api_get(path: str) -> dict[str, Any]:
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.get(f"{API_BASE}{path}")
        response.raise_for_status()
        return response.json()


def _api_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(f"{API_BASE}{path}", json=body or {})
        response.raise_for_status()
        return response.json()


def _sync_state() -> dict[str, Any]:
    return _api_get("/state")


def _state_items() -> list[StateItem]:
    keys = (
        "runs",
        "currentFailure",
        "proposedCorrections",
        "artifactHistory",
        "artifactProof",
        "evalDelta",
        "counterfactual",
        "weaveEvalBaseline",
        "weaveEvalPatched",
        "events",
        "budget",
        "operationTimings",
        "approvalState",
        "preflight",
    )
    return [StateItem(state_key=key, tool="*", tool_argument=key) for key in keys]


def _command_after(result: dict[str, Any]) -> Command:
    try:
        state = _sync_state()
    except Exception:
        state = {}
    return Command(update={**state, "lastToolResult": result})


@tool
def get_state() -> dict[str, Any]:
    """Read the full Loopie cockpit state from the REST API."""
    return _sync_state()


@tool
def reset_demo() -> Command:
    """Wipe Redis + Postgres back to a clean slate and reseed baseline artifacts."""
    return _command_after(_api_post("/reset"))


@tool
def seed() -> Command:
    """Seed Redis and Postgres with baseline flawed artifacts."""
    return _command_after(_api_post("/seed"))


@tool
def run_baseline(case_id: str = "security_001") -> Command:
    """Run baseline eval for a case (defaults to security_001 hero)."""
    return _command_after(_api_post("/run/baseline", {"case_id": case_id}))


@tool
def propose_corrections() -> Command:
    """Propose a structured correction for the current failure."""
    return _command_after(_api_post("/corrections/propose"))


@tool
def approve_correction(correction_id: str) -> Command:
    """Approve and apply a proposed correction by id."""
    return _command_after(_api_post("/corrections/approve", {"correction_id": correction_id}))


@tool
def run_patched(case_id: str = "security_001") -> Command:
    """Rerun the case after correction approval."""
    return _command_after(_api_post("/run/patched", {"case_id": case_id}))


@tool
def counterfactual_replay(hero_case_id: str = "security_001") -> Command:
    """Replay hero case and neighbors to prove no regression."""
    return _command_after(_api_post("/counterfactual", {"hero_case_id": hero_case_id}))


@tool
def get_artifact_history(key: str) -> list[dict[str, Any]]:
    """Return Postgres artifact version history for a key."""
    return _api_get(f"/artifacts/{key}")


@tool
def get_budget_status() -> dict[str, Any]:
    """Return token/cost budget status for the current pipeline."""
    return _api_get("/budget")


control_tools = [
    get_state,
    reset_demo,
    seed,
    run_baseline,
    propose_corrections,
    approve_correction,
    run_patched,
    counterfactual_replay,
    get_artifact_history,
    get_budget_status,
]


def build_unconfigured_chat_graph(reason: str):
    """Start without OpenAI so Render health checks pass until secrets are set."""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    def unavailable(state: MessagesState) -> dict[str, Any]:
        prior = state.get("messages", [])
        if prior and getattr(prior[-1], "type", "") == "ai":
            return {}
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"{reason} "
                        "Set OPENAI_API_KEY on the loopie-agent Render service to enable live GPT chat. "
                        "The reliability cockpit buttons still run the deterministic proof via loopie-api."
                    )
                )
            ]
        }

    builder = StateGraph(MessagesState)
    builder.add_node("unavailable", unavailable)
    builder.add_edge(START, "unavailable")
    builder.add_edge("unavailable", END)
    return builder.compile()


def build_control_agent():
    from langchain_openai import ChatOpenAI

    if not chat_api_key_configured():
        return build_unconfigured_chat_graph("Live chat is not configured.")

    ledger = Ledger.connect(strict=False)
    cost_callback = LedgerCostCallback(ledger=ledger)
    cfg = resolve_provider("supervisory") or provider_registry()["openai"]
    kwargs = openai_client_kwargs(cfg)
    if not is_gpt5_model(cfg.model):
        kwargs["model_kwargs"] = {"parallel_tool_calls": False}

    model = ChatOpenAI(**kwargs).with_config(
        callbacks=[cost_callback],
        run_name="loopie_control_chat",
    )

    return create_agent(
        model=model,
        tools=control_tools,
        middleware=[
            CopilotKitMiddleware(),
            StateStreamingMiddleware(*_state_items()),
        ],
        state_schema=LoopieControlState,
        system_prompt=(
            "You are the Loopie control agent. Use tools to read state, seed, run baseline, propose corrections, "
            "approve corrections, rerun patched evals, and counterfactual replay. "
            f"Live chat is metered (cap ${max_chat_cost_usd():.0f}). Keep responses brief. "
            f"If chat budget is exceeded, reply exactly: "
            f"{budget_degraded_message(max_chat_cost_usd(), max_chat_cost_usd())}"
        ),
    )


def build_graph():
    try:
        return build_control_agent()
    except ChatBudgetExceeded as exc:
        return build_unconfigured_chat_graph(handle_chat_budget_error(exc))
    except Exception as exc:
        return build_unconfigured_chat_graph(f"Live chat failed to start: {exc}")


graph = build_graph()
