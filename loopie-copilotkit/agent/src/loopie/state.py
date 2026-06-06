"""Loopie swarm state."""

from __future__ import annotations

from typing import Any, TypedDict


class LoopieState(TypedDict, total=False):
    ticket: dict[str, Any]
    retrieved_memory: dict[str, Any]
    routing_decision: str | None
    routing_rules: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    transitions: int
    action: str | None
    narration: dict[str, str]
    trace: list[dict[str, Any]]
    policy_checked: bool
    memory_version: int
    run_id: str
    budget_guard_triggered: bool
    mode: str
