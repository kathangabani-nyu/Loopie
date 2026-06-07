"""Runtime metering and paid-equivalent cost estimates for the cockpit."""

from __future__ import annotations

from typing import Any

from src.loopie.stores.ledger import Ledger

ESTIMATE_WALL_CLOCK_RATE_USD = 0.000003
ESTIMATE_TRACE_NODE_RATE_USD = 0.000002
ESTIMATE_EVAL_CASE_RATE_USD = 0.000001
ESTIMATE_BASIS = "wall_clock_ms + trace nodes + eval cases"


def sum_operation_ms(timings: list[dict[str, Any]]) -> float:
    return sum(float(entry.get("elapsed_ms") or 0) for entry in timings)


def iter_runs(state: dict[str, Any]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for entry in (state.get("runs") or {}).values():
        run = entry.get("run")
        if run:
            runs.append(run)
    counterfactual = state.get("counterfactual") or {}
    for entry in (counterfactual.get("results") or {}).values():
        run = entry.get("run")
        if run:
            runs.append(run)
    return runs


def sum_trace_node_ms(state: dict[str, Any]) -> float:
    total = 0.0
    for run in iter_runs(state):
        for step in run.get("trace") or []:
            total += float(step.get("duration_ms") or 0)
    return total


def fallback_wall_ms(state: dict[str, Any]) -> float:
    runs = iter_runs(state)
    if not runs:
        return 0.0
    latest_wall = max(float(run.get("wall_clock_ms") or 0) for run in runs)
    if latest_wall > 0:
        return latest_wall
    return sum_trace_node_ms(state)


def sum_trace_duration_ms(state: dict[str, Any]) -> float:
    return sum_trace_node_ms(state)


def count_trace_nodes(state: dict[str, Any]) -> int:
    count = 0
    for run in iter_runs(state):
        count += sum(1 for step in (run.get("trace") or []) if step.get("node"))
    return count


def count_eval_cases(state: dict[str, Any]) -> int:
    total = 0
    for key in ("weaveEvalBaseline", "weaveEvalPatched"):
        suite = state.get(key)
        if not suite:
            continue
        total += int(suite.get("total") or len(suite.get("results") or []))
    return total


def aggregate_pipeline_budget(state: dict[str, Any]) -> dict[str, Any]:
    llm_calls = 0
    transitions = 0
    estimated_cost_usd = 0.0
    for run in iter_runs(state):
        budget = run.get("budget") or {}
        llm_calls += int(budget.get("llm_calls") or 0)
        transitions = max(transitions, int(budget.get("transitions") or run.get("transitions") or 0))
        estimated_cost_usd += float(budget.get("estimated_cost_usd") or 0)
    return {
        "llm_calls": llm_calls,
        "transitions": transitions,
        "estimated_cost_usd": round(estimated_cost_usd, 6),
    }


def estimate_run_cost_usd(*, wall_clock_s: float, trace_node_count: int, eval_case_count: int) -> float:
    return round(
        (wall_clock_s * ESTIMATE_WALL_CLOCK_RATE_USD)
        + (trace_node_count * ESTIMATE_TRACE_NODE_RATE_USD)
        + (eval_case_count * ESTIMATE_EVAL_CASE_RATE_USD),
        6,
    )


def build_export_budget(
    state: dict[str, Any],
    ledger: Ledger,
    *,
    max_chat_cost_usd: float = 40.0,
) -> dict[str, Any]:
    timings = list(state.get("operationTimings") or [])
    pipeline_budget = aggregate_pipeline_budget(state)
    wall_ms = sum_operation_ms(timings)
    trace_ms = sum_trace_node_ms(state)
    if wall_ms <= 0:
        wall_ms = fallback_wall_ms(state)
    wall_clock_s = round(wall_ms / 1000.0, 3)
    node_time_s = round(trace_ms / 1000.0, 3)
    trace_node_count = count_trace_nodes(state)
    eval_case_count = count_eval_cases(state)
    estimated_run_cost_usd = estimate_run_cost_usd(
        wall_clock_s=wall_clock_s,
        trace_node_count=trace_node_count,
        eval_case_count=eval_case_count,
    )
    actual_model_cost_usd = round(
        ledger.total_cost() - ledger.total_cost(mode="chat"),
        6,
    )

    return {
        **pipeline_budget,
        "actual_model_cost_usd": actual_model_cost_usd,
        "estimated_run_cost_usd": estimated_run_cost_usd,
        "estimated_cost_usd": estimated_run_cost_usd,
        "wall_clock_s": wall_clock_s,
        "wall_clock_ms": round(wall_ms, 3),
        "node_time_s": node_time_s,
        "trace_node_count": trace_node_count,
        "eval_case_count": eval_case_count,
        "estimate_basis": ESTIMATE_BASIS,
        "chat_cost_usd": ledger.total_cost(mode="chat"),
        "max_chat_cost_usd": max_chat_cost_usd,
    }
