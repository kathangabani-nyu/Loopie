"""Weave evaluation helpers."""

from __future__ import annotations

from typing import Any

from src.loopie.config import get_settings
from src.loopie.reliability.scorers import run_passed, score_run
from src.loopie.runner import load_tickets, run_ticket
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore

_weave_initialized = False


def ensure_weave() -> None:
    global _weave_initialized
    if _weave_initialized:
        return
    try:
        import weave

        weave.init(get_settings().weave_project)
        _weave_initialized = True
    except Exception:
        _weave_initialized = False


def evaluate_suite(
    *,
    label: str,
    redis: RedisStore | None = None,
    ledger: Ledger | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    ensure_weave()
    redis = redis or RedisStore()
    ledger = ledger or Ledger.connect()
    tickets = load_tickets(limit=limit or get_settings().max_eval_cases_per_dev_run)
    results: list[dict[str, Any]] = []

    try:
        import weave

        weave.attributes({"iteration": label})
    except Exception:
        pass

    for ticket in tickets:
        run = run_ticket(ticket, redis=redis, ledger=ledger, artifact_version="v2" if label == "patched" else "v1")
        scores = score_run(run, ticket)
        passed = run_passed(scores)
        results.append({"case_id": ticket["case_id"], "passed": passed, "scores": scores, "action": run["action"]})

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "label": label,
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
    }
