"""CopilotKit assistant over the durable Loopie v1 product API."""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from copilotkit import CopilotKitMiddleware
from langchain.agents import create_agent
from langchain.tools import tool

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


def chat_api_key_configured() -> bool:
    provider = resolve_provider("supervisory") or provider_registry()["openai"]
    return bool(provider.api_key and str(provider.api_key).strip())


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    api_token = os.getenv("LOOPIE_API_TOKEN", "")
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def _api_get(path: str) -> Any:
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_headers()) as client:
        response = client.get(f"{API_BASE}{path}")
        response.raise_for_status()
        return response.json()


def _api_post(path: str, body: dict[str, Any] | None = None, *, idempotency_key: str | None = None) -> Any:
    headers = _headers({"Idempotency-Key": idempotency_key} if idempotency_key else None)
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=headers) as client:
        response = client.post(f"{API_BASE}{path}", json=body or {})
        response.raise_for_status()
        return response.json()


@tool
def list_tickets(limit: int = 20) -> list[dict[str, Any]]:
    """List recent production support tickets."""
    return _api_get(f"/api/v1/tickets?limit={min(max(limit, 1), 100)}")


@tool
def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """List durable runs and their explicit lifecycle status."""
    return _api_get(f"/api/v1/runs?limit={min(max(limit, 1), 100)}")


@tool
def get_run(run_id: str) -> dict[str, Any]:
    """Inspect one run, including its decision, correctness layers, manifest, and read set."""
    return _api_get(f"/api/v1/runs/{quote(run_id, safe='')}")


@tool
def list_failures(limit: int = 20) -> list[dict[str, Any]]:
    """List authoritative deterministic failure records."""
    return _api_get(f"/api/v1/failures?limit={min(max(limit, 1), 100)}")


@tool
def queue_ticket_run(ticket_id: str, mode: str = "live") -> dict[str, Any]:
    """Queue an idempotent run for an existing ticket; returns durable run and job handles."""
    if mode not in {"test", "live"}:
        raise ValueError("mode must be test or live")
    return _api_post(
        f"/api/v1/tickets/{quote(ticket_id, safe='')}/runs",
        {"mode": mode, "kind": "ticket"},
        idempotency_key=f"assistant:{ticket_id}:{uuid.uuid4()}",
    )


@tool
def propose_failure_correction(failure_id: str) -> dict[str, Any]:
    """Build and shadow-evaluate a validated correction for a known failure."""
    return _api_post(f"/api/v1/failures/{quote(failure_id, safe='')}/corrections")


@tool
def approve_correction(correction_id: str, human_confirmation: str) -> dict[str, Any]:
    """Apply a shadow-passing correction only after the human types APPROVE <correction_id>."""
    if human_confirmation.strip() != f"APPROVE {correction_id}":
        raise ValueError(f"Explicit confirmation required: APPROVE {correction_id}")
    return _api_post(
        f"/api/v1/corrections/{quote(correction_id, safe='')}/approve",
        {"actor": "owner", "channel": "hitl_chat"},
    )


@tool
def get_artifacts() -> list[dict[str, Any]]:
    """Read Postgres artifact Time Machine records and current versions."""
    return _api_get("/api/v1/artifacts")


@tool
def get_judge_calibration() -> dict[str, Any]:
    """Read judge agreement against golden annotations; judge flags are advisory only."""
    return _api_get("/api/v1/judge/calibration")


CONTROL_TOOLS = [
    list_tickets,
    list_runs,
    get_run,
    list_failures,
    queue_ticket_run,
    propose_failure_correction,
    approve_correction,
    get_artifacts,
    get_judge_calibration,
]


def build_unconfigured_chat_graph(reason: str, *, checkpointer=None):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    def unavailable(state: MessagesState) -> dict[str, Any]:
        prior = state.get("messages", [])
        if prior and getattr(prior[-1], "type", "") == "ai":
            return {}
        return {"messages": [AIMessage(content=f"{reason} Configure OPENAI_API_KEY on loopie-api.")]}

    builder = StateGraph(MessagesState)
    builder.add_node("unavailable", unavailable)
    builder.add_edge(START, "unavailable")
    builder.add_edge("unavailable", END)
    return builder.compile(checkpointer=checkpointer)


def build_control_agent(*, ledger: Ledger | None = None, checkpointer=None):
    from langchain_openai import ChatOpenAI

    if not chat_api_key_configured():
        return build_unconfigured_chat_graph("Live assistant is not configured.", checkpointer=checkpointer)
    ledger = ledger or Ledger.connect(strict=False)
    callback = LedgerCostCallback(ledger=ledger)
    provider = resolve_provider("supervisory") or provider_registry()["openai"]
    kwargs = openai_client_kwargs(provider)
    if not is_gpt5_model(provider.model):
        kwargs["model_kwargs"] = {"parallel_tool_calls": False}
    model = ChatOpenAI(**kwargs).with_config(callbacks=[callback], run_name="loopie_control_chat")
    return create_agent(
        model=model,
        tools=CONTROL_TOOLS,
        middleware=[CopilotKitMiddleware()],
        checkpointer=checkpointer,
        system_prompt=(
            "You are Loopie's reliability assistant. Read durable records before making claims. "
            "A deterministic policy/structural/golden failure is authoritative; judge flags are advisory. "
            "You may prepare a correction, but never call approve_correction unless the user explicitly "
            "typed the exact confirmation phrase in this conversation. Never invent score improvement. "
            f"Chat is metered (cap ${max_chat_cost_usd():.0f}). If exceeded, reply exactly: "
            f"{budget_degraded_message(max_chat_cost_usd(), max_chat_cost_usd())}"
        ),
    )


def build_graph(*, ledger: Ledger | None = None, checkpointer=None):
    try:
        return build_control_agent(ledger=ledger, checkpointer=checkpointer)
    except ChatBudgetExceeded as exc:
        return build_unconfigured_chat_graph(handle_chat_budget_error(exc), checkpointer=checkpointer)
    except Exception as exc:
        return build_unconfigured_chat_graph(f"Live assistant failed to start: {exc}", checkpointer=checkpointer)


graph = build_graph()
