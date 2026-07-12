"""Run a single ticket through the worker swarm (test or live)."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from src.loopie.config import get_settings, normalize_llm_mode
from src.loopie.decide import decide_tool_calls
from src.loopie.observability import current_weave_call_evidence, ensure_weave, op
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION
from src.loopie.manifests import ManifestReader, RunManifest, build_run_manifest
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


@op("run_ticket")
def _execute_run(
    ticket: dict[str, Any],
    *,
    run_id: str,
    project_id: str,
    redis: RedisStore,
    ledger: Ledger,
    mode: str | None,
    artifact_version: str,
    manifest: RunManifest | None = None,
    budget: BudgetTracker | None = None,
    eval_scope: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    mode = normalize_llm_mode(mode or settings.llm_mode)
    budget = budget or BudgetTracker()
    manifest = manifest or build_run_manifest(
        redis,
        ticket,
        prompt_version=DECISION_PROMPT_VERSION,
        schema_version=DECISION_SCHEMA_VERSION,
        model_version=settings.openai_model,
    )
    if manifest.ticket_id != str(ticket["case_id"]):
        raise ValueError("Pinned manifest ticket does not match the requested ticket")
    manifest_reader = ManifestReader(manifest)
    artifact_overrides: dict[str, Any] = {}

    if ticket.get("failure_seed") == "planner_loop":
        artifact_overrides["transitions"] = settings.max_agent_transitions

    ctx = RunContext(
        run_id=run_id,
        project_id=project_id,
        redis=redis,
        ledger=ledger,
        mode=mode,
        artifact_version=artifact_version,
        budget=budget,
        manifest=manifest,
        manifest_reader=manifest_reader,
        eval_scope=eval_scope,
        artifact_overrides=artifact_overrides,
        cost_events=[],
    )
    token = run_ctx.set(ctx)
    try:
        invoke_start = time.perf_counter()
        final_state = graph.invoke(
            {
                "ticket": ticket,
                "trace": [],
                "narration": {},
                "transitions": 0,
            }
        )
        wall_clock_ms = round((time.perf_counter() - invoke_start) * 1000, 3)
    finally:
        run_ctx.reset(token)

    artifacts = ctx.artifacts()
    action = final_state.get("action")
    if not action:
        if mode == "live":
            raise RuntimeError("Production run completed without an LLM decision")
        from src.loopie.reliability.oracle import decide_action

        action = decide_action(ticket, artifacts)
    if "tool_calls" in final_state:
        tool_calls = list(final_state.get("tool_calls") or [])
    elif mode == "live":
        raise RuntimeError("Production run completed without an effect-tool proposal record")
    else:
        tool_calls = decide_tool_calls(action)
    oracle_action = None
    if mode == "test":
        from src.loopie.reliability.oracle import decide_action

        oracle_action = decide_action(ticket, artifacts)
    memory = ctx.read_artifact("memory:policy:refund_window")
    trace = list(final_state.get("trace", []))
    swarm_nodes = [entry["node"] for entry in trace if entry.get("node") in SWARM_NODE_ORDER]

    run = {
        "run_id": run_id,
        "project_id": project_id,
        "case_id": ticket["case_id"],
        "action": action,
        "tool_calls": tool_calls,
        "transitions": budget.transitions,
        "max_transitions": int(artifacts.get("max_transitions", 6)),
        "policy_checked": bool(final_state.get("policy_checked", ticket.get("must_check_policy_version"))),
        "memory_version": int(final_state.get("memory_version", memory.get("version", 1))),
        "narration": final_state.get("narration", {}),
        "trace": trace,
        "wall_clock_ms": wall_clock_ms,
        "swarm_nodes": swarm_nodes,
        "execution_engine": final_state.get("execution_engine", "langgraph_swarm"),
        "mode": mode,
        "decided_by": final_state.get("decided_by", "oracle"),
        "fallback_used": bool(final_state.get("fallback_used", False)),
        "stop_reason": final_state.get("stop_reason", "test"),
        "decision_schema_version": final_state.get("decision_schema_version"),
        "prompt_version": final_state.get("prompt_version"),
        "cache_hit": bool(final_state.get("cache_hit", False)),
        "artifact_hash": manifest.content_hash[:16],
        "artifacts_snapshot": artifacts,
        "run_manifest": manifest.to_record(),
        "read_set": ctx.read_set(),
        "budget": budget.to_dict(),
        "budget_guard_triggered": bool(final_state.get("budget_guard_triggered", False)),
        "cost_events": list(ctx.cost_events or []),
        "audit_payload": dict(final_state.get("audit_payload") or {}),
        "tool_receipts": list(final_state.get("tool_receipts") or []),
        "evidence_calls": list(final_state.get("evidence_calls") or []),
        "decision_iterations": int(final_state.get("decision_iterations", 0)),
    }
    if oracle_action is not None:
        run["oracle_action"] = oracle_action
    weave_evidence = current_weave_call_evidence()
    if weave_evidence:
        run["weave"] = weave_evidence
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
    manifest: RunManifest | None = None,
    run_id: str | None = None,
    project_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, Any]:
    ensure_weave()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    return _execute_run(
        ticket,
        run_id=run_id or str(uuid.uuid4()),
        project_id=project_id,
        redis=redis,
        ledger=ledger,
        mode=mode,
        artifact_version=artifact_version,
        manifest=manifest,
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
    from src.loopie.policy.seeds import SEED_POLICIES
    from src.loopie.taxonomy import DEFAULT_ACTIONS

    redis.set_policy_rules([rule.model_dump(mode="json") for rule in SEED_POLICIES])
    redis.set_config("max_transitions", seed_memory.get("max_transitions", 6))
    redis.set_config("action_taxonomy", json.dumps(list(DEFAULT_ACTIONS)))

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
    ledger.append_artifact_version(
        artifact_key="policy:rules",
        version=1,
        value={"rules": [rule.model_dump(mode="json") for rule in SEED_POLICIES]},
        source_case="seed",
    )
    ledger.append_artifact_version(
        artifact_key="config:max_transitions",
        version=1,
        value={"key": "max_transitions", "value": seed_memory.get("max_transitions", 6)},
        source_case="seed",
    )
    ledger.append_artifact_version(
        artifact_key="config:action_taxonomy",
        version=1,
        value={"key": "action_taxonomy", "value": list(DEFAULT_ACTIONS)},
        source_case="seed",
    )
    redis.xadd("evals", {"event": "seed_complete"})
    ledger.record_audit("seed", {"memory": mem, "routing_rules": seed_rules.get("rules", [])})
    return {"seeded": True, "memory": mem, "routing_rules": seed_rules.get("rules", [])}
