"""Map Loopie scorer outcomes to Weave trace failure status (red nodes in the UI)."""

from __future__ import annotations

from typing import Any, Iterable

from src.loopie.observability import weave_tracing_enabled


class LoopieEvalFailure(Exception):
    """Deterministic eval failure — surfaced to Weave without aborting the swarm."""


def mark_current_call_failed(exc: BaseException) -> None:
    """Mark the in-flight Weave op as failed while still returning output to LangGraph."""
    if not weave_tracing_enabled():
        return
    try:
        import weave

        call = weave.get_current_call()
        if call is None:
            return
        client = weave.get_client()
        if client is not None:
            client.fail_call(call, exc)
    except Exception:
        # Observability must never break the reliability pipeline.
        return


def _plain_scores(run: dict[str, Any], ticket: dict[str, Any]) -> dict[str, bool]:
    """Score without invoking the traced score_run op (keeps fail_call on the swarm node)."""
    from src.loopie.reliability.scorers import SCORERS

    return {name: fn(run, ticket) for name, fn in SCORERS.items()}


def mark_run_scorer_failures(
    *,
    run: dict[str, Any],
    ticket: dict[str, Any],
    node: str,
    scorer_names: Iterable[str] | None = None,
    scores: dict[str, bool] | None = None,
) -> list[str]:
    """Fail the current Weave op when any named scorers fail for this partial run."""
    from src.loopie.reliability.scorers import SCORERS

    resolved = scores if scores is not None else _plain_scores(run, ticket)
    names = list(scorer_names) if scorer_names is not None else list(SCORERS)
    failed = [name for name in names if not resolved.get(name, True)]
    if not failed:
        return failed

    mark_current_call_failed(
        LoopieEvalFailure(
            f"{node} failed scorers: {', '.join(failed)} "
            f"(action={run.get('action')!r}, expected={ticket.get('expected_action')!r})"
        )
    )
    return failed
