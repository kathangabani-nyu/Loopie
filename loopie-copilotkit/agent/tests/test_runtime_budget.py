"""Runtime metering and paid-equivalent budget export tests."""

from memory_stores import MemoryLedger
from src.loopie.runtime_budget import (
    ESTIMATE_BASIS,
    build_export_budget,
    estimate_run_cost_usd,
    sum_operation_ms,
)


def test_estimate_run_cost_formula():
    cost = estimate_run_cost_usd(wall_clock_s=10.0, trace_node_count=5, eval_case_count=2)
    assert cost == round((10.0 * 0.000003) + (5 * 0.000002) + (2 * 0.000001), 6)


def test_build_export_budget_uses_operation_timings():
    ledger = MemoryLedger()
    state = {
        "operationTimings": [
            {"action": "baseline", "elapsed_ms": 1200.0},
            {"action": "patched", "elapsed_ms": 800.0},
        ],
        "runs": {
            "baseline_1": {
                "run": {
                    "trace": [
                        {"node": "triage", "duration_ms": 50},
                        {"node": "resolution", "duration_ms": 75},
                    ],
                    "wall_clock_ms": 900,
                }
            }
        },
    }
    budget = build_export_budget(state, ledger)
    assert budget["wall_clock_s"] == 2.0
    assert budget["node_time_s"] == 0.125
    assert budget["actual_model_cost_usd"] == 0.0
    assert budget["estimated_run_cost_usd"] > 0
    assert budget["estimated_cost_usd"] == budget["estimated_run_cost_usd"]
    assert budget["estimate_basis"] == ESTIMATE_BASIS


def test_build_export_budget_falls_back_to_trace_when_no_timings():
    ledger = MemoryLedger()
    state = {
        "operationTimings": [],
        "runs": {
            "baseline_1": {
                "run": {
                    "trace": [
                        {"node": "triage", "duration_ms": 100},
                        {"node": "resolution", "duration_ms": 200},
                    ],
                    "wall_clock_ms": 0,
                }
            }
        },
    }
    budget = build_export_budget(state, ledger)
    assert budget["wall_clock_s"] == 0.3
    assert budget["node_time_s"] == 0.3
    assert budget["estimated_run_cost_usd"] > 0


def test_sum_operation_ms():
    assert sum_operation_ms([{"elapsed_ms": 10}, {"elapsed_ms": 5.5}]) == 15.5
