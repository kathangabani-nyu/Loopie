"""Run a single ticket through the worker swarm (mock or live)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from src.loopie.config import get_settings
from src.loopie.decide import decide_action, decide_tool_calls
from src.loopie.llm import LLMGateway
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore
from src.loopie.tools import execute_tool

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_tickets(limit: int | None = None) -> list[dict[str, Any]]:
    tickets: list[dict[str, Any]] = []
    for line in (DATA_DIR / "tickets.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        import json

        tickets.append(json.loads(line))
        if limit and len(tickets) >= limit:
            break
    return tickets


def tickets_by_id(limit: int | None = None) -> dict[str, dict[str, Any]]:
    return {t["case_id"]: t for t in load_tickets(limit=limit)}


def run_ticket(
    ticket: dict[str, Any],
    *,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
    mode: str | None = None,
    artifact_version: str = "v1",
) -> dict[str, Any]:
    settings = get_settings()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    budget = BudgetTracker()
    gateway = LLMGateway(budget=budget, ledger=ledger)
    artifacts = redis.get_live_artifacts()

    if ticket.get("failure_seed") == "planner_loop":
        artifacts["transitions"] = settings.max_agent_transitions

    trace: list[dict[str, Any]] = []
    nodes = ["triage", "memory_lookup", "policy_check", "resolution", "evaluator"]
    narration: dict[str, str] = {}

    for node in nodes:
        budget.record_transition()
        if budget.budget_guard_triggered:
            break
        result = gateway.narrate(
            node=node,
            fixture_id=ticket["case_id"],
            artifact_version=artifact_version,
            ticket=ticket,
            artifacts=artifacts,
        )
        narration[node] = result.text
        trace.append({"node": node, "narration": result.text, "mode": result.mode})

    action = decide_action(ticket, artifacts)
    tool_calls = decide_tool_calls(action)
    for call in tool_calls:
        execute_tool(call["name"], {"ticket": ticket, "action": action})

    memory = redis.get_memory("policy:refund_window") or {"version": 1}
    run = {
        "run_id": str(uuid.uuid4()),
        "case_id": ticket["case_id"],
        "action": action,
        "tool_calls": tool_calls,
        "transitions": budget.transitions,
        "max_transitions": int(artifacts.get("max_transitions", 6)),
        "policy_checked": bool(ticket.get("must_check_policy_version")),
        "memory_version": int(memory.get("version", 1)),
        "narration": narration,
        "trace": trace,
        "mode": mode or settings.llm_mode,
        "budget": budget.to_dict(),
    }
    redis.xadd("swarm", {"event": "run_completed", "case_id": ticket["case_id"], "action": action})
    return run


def seed_baseline(*, redis: RedisStore | None = None, ledger: Ledger | None = None) -> dict[str, Any]:
    import json

    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    seed_memory = json.loads((DATA_DIR / "seed_memory.json").read_text(encoding="utf-8"))
    seed_rules = json.loads((DATA_DIR / "seed_routing_rules.json").read_text(encoding="utf-8"))

    mem = seed_memory["memory"]
    redis.set_memory(mem["key"], mem["value"], version=mem["version"])
    redis.set_routing_rules(seed_rules.get("rules", []))
    redis.set_config("max_transitions", seed_memory.get("max_transitions", 6))

    ledger.append_artifact_version(
        artifact_key=f"memory:{mem['key']}",
        version=mem["version"],
        value=mem,
        source_case="seed",
    )
    ledger.append_artifact_version(
        artifact_key="routing:rules",
        version=1,
        value=seed_rules,
        source_case="seed",
    )
    redis.xadd("evals", {"event": "seed_complete"})
    ledger.record_audit("seed", {"memory": mem, "routing_rules": seed_rules.get("rules", [])})
    return {"seeded": True, "memory": mem, "routing_rules": seed_rules.get("rules", [])}
