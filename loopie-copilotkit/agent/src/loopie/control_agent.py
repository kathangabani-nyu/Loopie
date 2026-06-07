"""CopilotKit control agent for the Loopie cockpit."""

from __future__ import annotations

from typing import Any

from copilotkit import CopilotKitMiddleware, StateStreamingMiddleware, StateItem
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.types import Command
from typing_extensions import TypedDict

from src.loopie.pipeline import LoopiePipeline

_pipeline = LoopiePipeline()


class LoopieControlState(TypedDict, total=False):
    runs: dict[str, Any]
    currentFailure: dict[str, Any] | None
    proposedCorrections: list[dict[str, Any]]
    artifactHistory: list[dict[str, Any]]
    artifactProof: dict[str, Any] | None
    evalDelta: dict[str, Any]
    counterfactual: dict[str, Any]
    events: list[dict[str, Any]]
    budget: dict[str, Any]
    approvalState: str


def _sync_state() -> dict[str, Any]:
    return _pipeline.export_state()


def _state_items() -> list[StateItem]:
    keys = (
        "runs",
        "currentFailure",
        "proposedCorrections",
        "artifactHistory",
        "artifactProof",
        "evalDelta",
        "counterfactual",
        "events",
        "budget",
        "approvalState",
    )
    return [StateItem(state_key=key, tool="*", tool_argument=key) for key in keys]


def _command_after(result: dict[str, Any]) -> Command:
    return Command(update={**_sync_state(), "lastToolResult": result})


@tool
def reset_demo() -> Command:
    """Wipe Redis + Postgres back to a clean slate and reseed baseline artifacts."""
    return _command_after(_pipeline.reset())


@tool
def seed() -> Command:
    """Seed Redis and Postgres with baseline flawed artifacts."""
    return _command_after(_pipeline.seed())


@tool
def run_baseline(case_id: str = "security_001") -> Command:
    """Run baseline eval for a case (defaults to security_001 hero)."""
    return _command_after(_pipeline.run_baseline(case_id=case_id))


@tool
def propose_corrections() -> Command:
    """Propose a structured correction for the current failure."""
    return _command_after(_pipeline.propose_corrections())


@tool
def approve_correction(correction_id: str) -> Command:
    """Approve and apply a proposed correction by id."""
    return _command_after(_pipeline.approve_correction(correction_id))


@tool
def run_patched(case_id: str = "security_001") -> Command:
    """Rerun the case after correction approval."""
    return _command_after(_pipeline.run_patched(case_id=case_id))


@tool
def counterfactual_replay(hero_case_id: str = "security_001") -> Command:
    """Replay hero case and neighbors to prove no regression."""
    return _command_after(_pipeline.counterfactual_replay_suite(hero_case_id=hero_case_id))


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
            StateStreamingMiddleware(*_state_items()),
        ],
        state_schema=LoopieControlState,
        system_prompt=(
            "You are the Loopie control agent. Use tools to seed, run baseline, propose corrections, "
            "approve corrections, rerun patched evals, and counterfactual replay. Keep responses brief."
        ),
    )


graph = build_control_agent()
