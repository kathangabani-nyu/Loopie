"""Counterfactual replay and no-regression checks."""

from __future__ import annotations

from typing import Any, Callable


def counterfactual_replay(
    *,
    hero_case_id: str,
    neighbor_case_ids: list[str],
    run_case: Callable[[dict[str, Any]], dict[str, Any]],
    tickets_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    newly_failing: list[str] = []
    for case_id in [hero_case_id, *neighbor_case_ids]:
        ticket = tickets_by_id[case_id]
        run = run_case(ticket)
        from src.loopie.reliability.scorers import run_passed, score_run

        scores = score_run(run, ticket)
        passed = run_passed(scores)
        results[case_id] = {"run": run, "scores": scores, "passed": passed}
        if not passed and not ticket.get("expected_failure_baseline"):
            newly_failing.append(case_id)
    return {
        "results": results,
        "newly_failing": newly_failing,
        "no_regression": len(newly_failing) == 0,
    }
