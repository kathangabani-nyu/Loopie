"""Weave evaluation helpers."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any, Callable

from src.loopie.artifacts import apply_seed_artifacts_to_redis
from src.loopie.config import get_settings
from src.loopie.observability import ensure_weave, op
from src.loopie.reliability.budget import BudgetTracker
from src.loopie.reliability.scorers import SCORERS, run_passed, score_run
from src.loopie.runner import load_tickets, run_ticket
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


def _case_family(case_id: str) -> str:
    if case_id.startswith("security"):
        return "security"
    if case_id.startswith("refund"):
        return "refund"
    if case_id.startswith("loop"):
        return "loop"
    if case_id.startswith("memory"):
        return "memory"
    if case_id.startswith("tool"):
        return "tool"
    return "other"


def _make_scorer(name: str):
    fn = SCORERS[name]

    @op(f"scorer.{name}")
    def scorer(ticket: dict[str, Any], output: dict[str, Any]) -> dict[str, bool | str]:
        return {name: fn(output, ticket)}

    scorer.__name__ = f"scorer_{name}"
    return scorer


_EVAL_SCORERS = [_make_scorer(name) for name in SCORERS]


def _build_weave_scorers() -> list[Any]:
    """Build real weave.op scorers for weave.Evaluation."""
    import weave

    def make_scorer(name: str, fn: Callable[[dict[str, Any], dict[str, Any]], bool]):
        @weave.op(name=f"scorer.{name}")
        def scorer(output: dict[str, Any], ticket: dict[str, Any]) -> dict[str, bool]:
            return {name: fn(output, ticket)}

        return scorer

    return [make_scorer(name, fn) for name, fn in SCORERS.items()]


def _build_weave_predictor(ctx: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """Return a weave.op predictor required by weave.Evaluation.evaluate()."""
    import weave

    @weave.op(name="evals.predict_row")
    def predict_row(ticket: dict[str, Any], artifact_version: str) -> dict[str, Any]:
        run = run_ticket(
            ticket,
            redis=ctx["redis"],
            ledger=ctx["ledger"],
            mode=ctx.get("mode"),
            artifact_version=artifact_version,
            budget=ctx["budget"],
            eval_scope=True,
        )
        ctx["runs_by_case"][ticket["case_id"]] = run
        return run

    return predict_row


def _run_manual_suite(
    *,
    tickets: list[dict[str, Any]],
    dataset: list[dict[str, Any]],
    redis: RedisStore,
    ledger: Ledger,
    artifact_version: str,
    mode: str | None,
    runs_by_case: dict[str, dict[str, Any]],
) -> BudgetTracker:
    eval_budget = BudgetTracker()
    for ticket in tickets:
        run = run_ticket(
            ticket,
            redis=redis,
            ledger=ledger,
            mode=mode,
            artifact_version=artifact_version,
            budget=eval_budget,
            eval_scope=True,
        )
        runs_by_case[ticket["case_id"]] = run
    return eval_budget


def evaluate_suite(
    *,
    label: str,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
    limit: int | None = None,
    correction_id: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    ensure_weave()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    settings = get_settings()
    effective_mode = mode or settings.llm_mode
    tickets = load_tickets(limit=limit or settings.max_eval_cases_per_dev_run)
    artifact_version = "v2" if label == "patched" else "v1"

    if label == "baseline":
        apply_seed_artifacts_to_redis(redis)

    dataset = [
        {
            "ticket": ticket,
            "artifact_version": artifact_version,
            "case_id": ticket["case_id"],
            "expected_action": ticket.get("expected_action"),
            "case_family": _case_family(ticket["case_id"]),
        }
        for ticket in tickets
    ]

    results: list[dict[str, Any]] = []
    weave_eval_id: str | None = None
    weave_eval_error: str | None = None
    weave_eval_used_manual_fallback = False
    runs_by_case: dict[str, dict[str, Any]] = {}

    def _collect_results() -> None:
        results.clear()
        for row in dataset:
            ticket = row["ticket"]
            run = runs_by_case[ticket["case_id"]]
            scores = score_run(run, ticket)
            passed = run_passed(scores)
            results.append(
                {
                    "case_id": ticket["case_id"],
                    "passed": passed,
                    "scores": scores,
                    "action": run["action"],
                    "decided_by": run.get("decided_by"),
                    "fallback_used": run.get("fallback_used", False),
                }
            )

    use_weave_eval = effective_mode == "live" and bool(os.getenv("WANDB_API_KEY"))

    if use_weave_eval:
        import weave

        eval_budget = BudgetTracker()
        ctx: dict[str, Any] = {
            "redis": redis,
            "ledger": ledger,
            "mode": effective_mode,
            "budget": eval_budget,
            "runs_by_case": runs_by_case,
        }
        predictor = _build_weave_predictor(ctx)

        attrs = {
            "iteration": label,
            "artifact_version": artifact_version,
            "case_family": "suite",
        }
        if correction_id:
            attrs["correction_id"] = correction_id

        evaluation = weave.Evaluation(
            name=f"loopie_{label}",
            dataset=dataset,
            scorers=_build_weave_scorers(),
            preprocess_model_input=lambda row: {
                "ticket": row["ticket"],
                "artifact_version": row["artifact_version"],
            },
            evaluation_name=f"loopie_{label}_{artifact_version}",
        )

        eval_coro = None
        try:
            with weave.attributes(attrs):
                eval_coro = evaluation.evaluate(predictor)
                eval_result = asyncio.run(eval_coro)
            weave_eval_id = str(getattr(eval_result, "id", None) or eval_result)
            _collect_results()
        except Exception as exc:
            if inspect.iscoroutine(eval_coro):
                eval_coro.close()
            weave_eval_error = f"{type(exc).__name__}: {exc}"
            runs_by_case.clear()
    else:
        weave_eval_used_manual_fallback = True
        _run_manual_suite(
            tickets=tickets,
            dataset=dataset,
            redis=redis,
            ledger=ledger,
            artifact_version=artifact_version,
            mode=effective_mode,
            runs_by_case=runs_by_case,
        )
        _collect_results()

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "label": label,
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
        "artifact_version": artifact_version,
        "weave_eval_id": weave_eval_id,
        "weave_eval_error": weave_eval_error,
        "weave_eval_used_manual_fallback": weave_eval_used_manual_fallback,
        "artifacts_rewound": label == "baseline",
    }
