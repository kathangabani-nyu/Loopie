"""In-process CopilotKit AG-UI endpoint for the Loopie control graph."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import LangGraphAGUIAgent
from fastapi import FastAPI


def _make_agent(graph: Any) -> LangGraphAGUIAgent:
    return LangGraphAGUIAgent(
        name="loopie_control",
        description="Investigate Loopie runs and operate the reliability control plane.",
        graph=graph,
    )


@lru_cache(maxsize=1)
def _production_agent() -> LangGraphAGUIAgent:
    # Lazy construction keeps health checks and route import independent of LLM/store
    # configuration. The first authenticated Copilot request resolves the graph.
    from src.loopie.checkpointing import get_checkpoint_runtime
    from src.loopie.control_agent import build_graph

    runtime = get_checkpoint_runtime()
    return _make_agent(build_graph(checkpointer=runtime.checkpointer))


def mount_copilotkit(
    app: FastAPI,
    *,
    graph: Any | None = None,
    path: str = "/api/copilotkit/agent/loopie_control",
):
    """Mount the native AG-UI endpoint and return its request-isolated agent."""

    agent = _production_agent() if graph is None else _make_agent(graph)
    add_langgraph_fastapi_endpoint(app, agent, path)
    return agent
