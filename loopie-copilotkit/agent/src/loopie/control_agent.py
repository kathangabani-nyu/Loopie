"""CopilotKit control agent for the Loopie cockpit."""

from __future__ import annotations

from typing import Any

from copilotkit import CopilotKitMiddleware, StateStreamingMiddleware, StateItem
from langchain.agents import create_agent
from langchain.tools import tool
from typing_extensions import TypedDict

from src.loopie.pipeline import LoopiePipeline

_pipeline = LoopiePipeline()


class LoopieControlState(TypedDict, total=False):
    runs: dict[str, Any]
    currentFailure: dict[str, Any] | None
    proposedCorrections: list[dict[str, Any]]
    artifactHistory: list[dict[str, Any]]
    evalDelta: dict[str, Any]
    counterfactual: dict[str, Any]
    events: list[dict[str, Any]]
    budget: dict[str, Any]
    approvalState: str


def _sync_state() -> dict[str, Any]:
    return _pipeline.export_state()


@tool
def reset_demo() -> dict[str, Any]:
    """Wipe Redis + Postgres back to a clean slate and reseed baseline artifacts."""
    result = _pipeline.reset()
    _sync_state()
    return result


@tool
def seed() -> dict[str, Any]:
    """Seed Redis and Postgres with baseline flawed artifacts."""
    result = _pipeline.seed()
    _sync_state()
    return result


@tool
def run_baseline(case_id: str = "security_001") -> dict[str, Any]:
    """Run baseline eval for a case (defaults to security_001 hero)."""
    result = _pipeline.run_baseline(case_id=case_id)
    _sync_state()
    return result


@tool
def propose_corrections() -> dict[str, Any]:
    """Propose a structured correction for the current failure."""
    result = _pipeline.propose_corrections()
    _sync_state()
    return result


@tool
def approve_correction(correction_id: str) -> dict[str, Any]:
    """Approve and apply a proposed correction by id."""
    result = _pipeline.approve_correction(correction_id)
    _sync_state()
    return result


@tool
def run_patched(case_id: str = "security_001") -> dict[str, Any]:
    """Rerun the case after correction approval."""
    result = _pipeline.run_patched(case_id=case_id)
    _sync_state()
    return result


@tool
def counterfactual_replay(hero_case_id: str = "security_001") -> dict[str, Any]:
    """Replay hero case and neighbors to prove no regression."""
    result = _pipeline.counterfactual_replay_suite(hero_case_id=hero_case_id)
    _sync_state()
    return result


@tool
def get_artifact_history(key: str) -> list[dict[str, Any]]:
    """Return Postgres artifact version history for a key."""
    return _pipeline.get_artifact_history(key)


@tool
def get_budget_status() -> dict[str, Any]:
    """Return token/cost budget status for the current pipeline."""
    return _pipeline.get_budget_status()


control_tools = [
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


def build_control_agent():
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model="gpt-4o-mini", model_kwargs={"parallel_tool_calls": False})
    return create_agent(
        model=model,
        tools=control_tools,
        middleware=[
            CopilotKitMiddleware(),
            StateStreamingMiddleware(
                StateItem(state_key="runs", tool="run_baseline", tool_argument="runs"),
            ),
        ],
        state_schema=LoopieControlState,
        system_prompt=(
            "You are the Loopie control agent. Use tools to seed, run baseline, propose corrections, "
            "approve corrections, rerun patched evals, and counterfactual replay. Keep responses brief."
        ),
    )


graph = build_control_agent()
