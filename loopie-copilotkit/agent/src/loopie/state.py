"""Loopie swarm state."""

from __future__ import annotations

from typing import Any, TypedDict


class LoopieState(TypedDict, total=False):
    ticket: dict[str, Any]
    retrieved_memory: dict[str, Any]
    routing_decision: str | None
    routing_rules: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    proposed_tools: list[dict[str, Any]]
    evidence_calls: list[dict[str, Any]]
    decision_iterations: int
    transitions: int
    action: str | None
    model_action: str | None
    policy_enforced: bool
    policy_overrode_action: bool
    policy_enforced_by: list[str]
    narration: dict[str, str]
    trace: list[dict[str, Any]]
    policy_checked: bool
    memory_version: int
    run_id: str
    budget_guard_triggered: bool
    mode: str
    decided_by: str
    fallback_used: bool
    stop_reason: str
    decision_schema_version: str
    prompt_version: str
    cache_hit: bool
    execution_engine: str
    audit_event_id: int | None
    audit_payload: dict[str, Any]
    tool_receipts: list[dict[str, Any]]
