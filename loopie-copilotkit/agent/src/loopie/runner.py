"""Run a single ticket through the worker swarm (mock or live)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from src.loopie.artifacts import artifact_content_hash
from src.loopie.config import get_settings
from src.loopie.decide import LIVE_DECISION_CASES, decide_action, decide_tool_calls
from src.loopie.observability import ensure_weave, op
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.run_context import RunContext, run_ctx
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore
from src.loopie.swarm import SWARM_NODE_ORDER, graph

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


def _uses_live_decision(ticket: dict[str, Any], mode: str | None, settings: Any) -> bool:
    effective_mode = (mode or settings.llm_mode).strip().lower()
    if effective_mode != "live":
        return False
    if settings.full_agentic:
        return True
    return ticket.get("case_id") in LIVE_DECISION_CASES


@op("run_ticket")
def _execute_run(
    ticket: dict[str, Any],
    *,
    redis: RedisStore,
    ledger: Ledger,
    mode: str | None,
    artifact_version: str,
    budget: BudgetTracker | None = None,
    eval_scope: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    budget = budget or BudgetTracker()
    artifacts = redis.get_live_artifacts()

    if ticket.get("failure_seed") == "planner_loop":
        artifacts["transitions"] = settings.max_agent_transitions
    artifacts_hash = artifact_content_hash(artifacts)

    ctx = RunContext(
        redis=redis,
        ledger=ledger,
        mode=mode,
        artifact_version=artifact_version,
        budget=budget,
        eval_scope=eval_scope,
        artifacts=artifacts,
    )
    token = run_ctx.set(ctx)
    try:
        final_state = graph.invoke(
            {
                "ticket": ticket,
                "trace": [],
                "narration": {},
                "transitions": 0,
            }
        )
    finally:
        run_ctx.reset(token)

    action = final_state.get("action") or decide_action(ticket, artifacts)
    tool_calls = final_state.get("tool_calls") or decide_tool_calls(action)
    oracle_action = decide_action(ticket, artifacts)
    memory = redis.get_memory("policy:refund_window") or {"version": 1}
    trace = list(final_state.get("trace", []))
    swarm_nodes = [entry["node"] for entry in trace if entry.get("node") in SWARM_NODE_ORDER]

    run = {
        "run_id": str(uuid.uuid4()),
        "case_id": ticket["case_id"],
        "action": action,
        "tool_calls": tool_calls,
        "transitions": budget.transitions,
        "max_transitions": int(artifacts.get("max_transitions", 6)),
        "policy_checked": bool(final_state.get("policy_checked", ticket.get("must_check_policy_version"))),
        "memory_version": int(final_state.get("memory_version", memory.get("version", 1))),
        "narration": final_state.get("narration", {}),
        "trace": trace,
        "swarm_nodes": swarm_nodes,
        "execution_engine": final_state.get("execution_engine", "langgraph_swarm"),
        "mode": mode or settings.llm_mode,
        "decided_by": final_state.get("decided_by", "oracle"),
        "fallback_used": bool(final_state.get("fallback_used", False)),
        "stop_reason": final_state.get("stop_reason", "mock"),
        "decision_schema_version": final_state.get("decision_schema_version"),
        "prompt_version": final_state.get("prompt_version"),
        "cache_hit": bool(final_state.get("cache_hit", False)),
        "artifact_hash": artifacts_hash,
        "oracle_action": oracle_action,
        "artifacts_snapshot": artifacts,
        "budget": budget.to_dict(),
        "budget_guard_triggered": bool(final_state.get("budget_guard_triggered", False)),
    }
    redis.xadd("swarm", {"event": "run_completed", "case_id": ticket["case_id"], "action": action})
    return run


def run_ticket(
    ticket: dict[str, Any],
    *,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
    mode: str | None = None,
    artifact_version: str = "v1",
    budget: BudgetTracker | None = None,
    eval_scope: bool = False,
) -> dict[str, Any]:
    ensure_weave()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    return _execute_run(
        ticket,
        redis=redis,
        ledger=ledger,
        mode=mode,
        artifact_version=artifact_version,
        budget=budget,
        eval_scope=eval_scope,
    )


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
