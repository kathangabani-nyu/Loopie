"""Native Weave Evaluation logging for the current Golden Demo workflow."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from src.loopie.observability import ensure_weave, weave_tracing_enabled


def case_family(case_id: str) -> str:
    return case_id.split("_", 1)[0] if "_" in case_id else "other"


def evaluation_row(ticket: dict[str, Any]) -> dict[str, Any]:
    """Return stable, non-sensitive inputs shared by comparable evaluations."""

    case_id = str(ticket.get("case_id") or ticket.get("external_id") or "unknown")
    return {
        "case_id": case_id,
        "case_family": case_family(case_id),
        "expected_action": ticket.get("expected_action"),
        "required_policy_rule_ids": list(ticket.get("required_policy_rule_ids") or []),
    }


def compact_evaluation_output(
    run: dict[str, Any],
    *,
    sample: int | None = None,
) -> dict[str, Any]:
    """Keep native evaluation rows concise while linking them to full run traces."""

    output = {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "phase": run.get("phase"),
        "action": run.get("action"),
        "model_action": run.get("model_action"),
        "policy_enforced_by": list(run.get("policy_enforced_by") or []),
        "mode": run.get("mode"),
        "decided_by": run.get("decided_by"),
        "fallback_used": bool(run.get("fallback_used", False)),
        "wall_clock_ms": run.get("wall_clock_ms"),
        "weave_trace_url": (run.get("weave") or {}).get("url"),
    }
    if sample is not None:
        output["sample"] = sample
    return output


def flatten_correctness(correctness: dict[str, Any]) -> dict[str, bool]:
    """Flatten authoritative correctness layers into Weave-native score columns."""

    scores: dict[str, bool] = {"passed": bool(correctness.get("passed"))}
    policy = correctness.get("policy") or {}
    structural = correctness.get("structural") or {}
    golden = correctness.get("golden") or {}
    scores["policy_passed"] = bool(policy.get("passed"))
    scores["structural_passed"] = bool(structural.get("passed"))
    if correctness.get("golden") is not None:
        scores["golden_passed"] = bool(golden.get("passed"))
    for name, value in (structural.get("scores") or {}).items():
        scores[f"structural_{name}"] = bool(value)
    for name, value in (golden.get("scores") or {}).items():
        scores[f"golden_{name}"] = bool(value)
    return scores


NATIVE_SCORER_NAMES = [
    "passed",
    "policy_passed",
    "structural_passed",
    "golden_passed",
    "structural_action_in_taxonomy",
    "structural_loop_count_under_limit",
    "structural_tool_calls_under_budget",
    "structural_production_decision_completed",
    "golden_action_match",
    "golden_required_policy_checked",
    "golden_memory_version_correct",
    "golden_required_policy_rules_present",
]


@dataclass
class NativeEvaluationSession:
    """Failure-isolated wrapper around Weave's incremental EvaluationLogger."""

    name: str
    logger: Any | None
    status: str
    error: str | None = None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        dataset_name: str,
        dataset_rows: list[dict[str, Any]],
        model: dict[str, Any] | str,
        attributes: dict[str, Any],
        enabled: bool,
    ) -> "NativeEvaluationSession":
        if not enabled or not weave_tracing_enabled():
            return cls(name=name, logger=None, status="disabled")
        if not ensure_weave():
            return cls(name=name, logger=None, status="failed", error="Weave is not ready")
        try:
            import weave

            dataset = weave.Dataset(name=dataset_name, rows=dataset_rows)
            logger = weave.EvaluationLogger(
                name=name,
                model=model,
                dataset=dataset,
                eval_attributes=attributes,
                scorers=NATIVE_SCORER_NAMES,
            )
            return cls(name=name, logger=logger, status="recording")
        except Exception as exc:
            return cls(
                name=name,
                logger=None,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _capture_error(self, exc: BaseException) -> None:
        self.status = "failed"
        self.error = f"{type(exc).__name__}: {exc}"

    @contextmanager
    def prediction(self, inputs: dict[str, Any]) -> Iterator[Any | None]:
        """Nest an existing model execution under one native evaluation row."""

        if self.logger is None:
            yield None
            return
        try:
            prediction = self.logger.log_prediction(inputs=inputs)
            prediction.__enter__()
        except Exception as exc:
            self._capture_error(exc)
            yield None
            return
        try:
            yield prediction
        except BaseException as exc:
            try:
                prediction.__exit__(type(exc), exc, exc.__traceback__)
                self.logger.fail(exc)
            except Exception as log_exc:
                self._capture_error(log_exc)
            raise
        else:
            try:
                prediction.__exit__(None, None, None)
            except Exception as exc:
                self._capture_error(exc)

    def record(
        self,
        prediction: Any | None,
        *,
        output: dict[str, Any],
        scores: dict[str, bool],
    ) -> None:
        if prediction is None:
            return
        try:
            prediction.output = output
            for scorer, score in scores.items():
                prediction.log_score(scorer, score)
        except Exception as exc:
            self._capture_error(exc)

    def finish(self, summary: dict[str, Any]) -> dict[str, Any]:
        if self.logger is not None and self.status != "failed":
            try:
                self.logger.log_summary(summary)
                self.status = "published"
            except Exception as exc:
                self._capture_error(exc)
        url = None
        if self.logger is not None:
            try:
                url = self.logger.ui_url
            except Exception as exc:
                self._capture_error(exc)
        if self.status == "published" and not url:
            self.status = "failed"
            self.error = "Weave evaluation completed without a UI URL"
        return {
            "name": self.name,
            "status": self.status,
            "url": str(url) if url else None,
            "error": self.error,
        }


def create_native_evaluation(**kwargs: Any) -> NativeEvaluationSession:
    """Small indirection that keeps worker and shadow-gate tests easy to isolate."""

    return NativeEvaluationSession.create(**kwargs)
