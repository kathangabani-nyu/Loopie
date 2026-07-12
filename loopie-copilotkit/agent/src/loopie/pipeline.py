"""Golden regression harness for the original deterministic demo contract.

Production requests never import this module; they use services.RunService and
services.ApprovalService. The harness remains only to prove historical golden
behavior and the cross-phase fail/correct/rerun invariant in CI.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, TypeVar

from src.loopie.artifacts import apply_seed_artifacts_to_redis
from src.loopie.config import get_settings
from src.loopie.reliability.classifier import classify_failure
from src.loopie.reliability.corrections import (
    apply,
    prepare_correction,
    shadow_evaluate_correction,
)
from src.loopie.reliability.diagnosis import agentic_diagnosis
from src.loopie.reliability.scorers import live_decision_honest, oracle_match
from src.loopie.reliability.replay import counterfactual_replay
from src.loopie.reliability.scorers import run_passed, score_run
from src.loopie.runner import run_ticket, seed_baseline, tickets_by_id
from src.loopie.preflight import run_preflight
from src.loopie.runtime_budget import build_export_budget
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore

T = TypeVar("T")


class LoopiePipeline:
    def __init__(self) -> None:
        settings = get_settings()
        self.redis = RedisStore()
        self.ledger = Ledger.connect(strict=settings.hosted)
        if settings.hosted:
            from src.loopie.preflight import assert_hosted_ready

            self.preflight = assert_hosted_ready(redis=self.redis, ledger=self.ledger)
        else:
            self.preflight = run_preflight(redis=self.redis, ledger=self.ledger)
        self.state: dict[str, Any] = self._initial_state()

    def _llm_mode(self) -> str:
        return get_settings().llm_mode

    def _refresh_settings(self) -> None:
        get_settings.cache_clear()

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "runs": {},
            "currentFailure": None,
            "proposedCorrections": [],
            "artifactHistory": [],
            "artifactProof": None,
            "evalDelta": {},
            "counterfactual": {},
            "events": [],
            "budget": {},
            "operationTimings": [],
            "approvalState": "idle",
        }

    def _refresh_export_budget(self) -> None:
        import os

        self.state["budget"] = build_export_budget(
            self.state,
            self.ledger,
            max_chat_cost_usd=float(os.getenv("LOOPIE_MAX_CHAT_COST_USD", "40")),
        )

    @contextmanager
    def _operation_timer(self, action: str) -> Iterator[None]:
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        yield
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
        finished_at = datetime.now(timezone.utc).isoformat()
        self.state.setdefault("operationTimings", []).append(
            {
                "action": action,
                "elapsed_ms": elapsed_ms,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
        self._refresh_export_budget()

    def _timed(self, action: str, fn: Callable[[], T]) -> T:
        with self._operation_timer(action):
            return fn()

    def reset(self) -> dict[str, Any]:
        """Wipe Redis + ledger back to a clean slate and reseed baseline artifacts."""

        def _do_reset() -> dict[str, Any]:
            self.redis.flush_loopie_keys()
            self.ledger.reset()
            self.state = self._initial_state()
            seeded = self.seed()
            return {"reset": True, **seeded}

        return self._timed("reset", _do_reset)

    def seed(self) -> dict[str, Any]:
        result = seed_baseline(redis=self.redis, ledger=self.ledger)
        self.state["events"] = self.redis.xread_recent("evals")
        return result

    def _begin_baseline_leg(self) -> None:
        """Rewind Redis to seed artifacts and clear downstream demo state."""
        apply_seed_artifacts_to_redis(self.redis)
        self.state["currentFailure"] = None
        self.state["proposedCorrections"] = []
        self.state["approvalState"] = "idle"
        self.state["artifactProof"] = None
        self.state["evalDelta"] = {}
        self.state["counterfactual"] = {}
        self.state["runs"] = {}
        self.state.pop("weaveEvalBaseline", None)
        self.state.pop("weaveEvalPatched", None)

    def run_baseline(self, *, case_id: str = "security_001") -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            self._begin_baseline_leg()
            ticket = tickets_by_id()[case_id]
            run = run_ticket(ticket, redis=self.redis, ledger=self.ledger, mode=self._llm_mode())
            scores = score_run(run, ticket)
            passed = run_passed(scores)
            failure = None
            if not passed:
                category = classify_failure(scores, ticket)
                failure = {"case_id": case_id, "scores": scores, "category": category, "run": run}
                self.state["currentFailure"] = failure
            else:
                self.state["currentFailure"] = None
            eval_run_id = f"baseline_{uuid.uuid4().hex[:8]}"
            self.state["runs"][eval_run_id] = {
                "label": "baseline",
                "case_id": case_id,
                "run": run,
                "scores": scores,
            }
            self.redis.xadd("evals", {"event": "baseline_complete", "case_id": case_id, "passed": passed})
            return {
                "eval_run_id": eval_run_id,
                "passed": passed,
                "scores": scores,
                "failure": failure,
            }

        payload = self._timed("baseline", _do)
        if not payload.get("passed") and payload.get("failure"):
            weave_eval = self._run_weave_eval_if_enabled(label="baseline")
            if weave_eval is not None:
                payload["weave_eval"] = weave_eval
        return payload

    def _run_weave_eval_if_enabled(
        self,
        *,
        label: str,
        correction_id: str | None = None,
        artifact_proof: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not get_settings().weave_enabled:
            return None

        def _do() -> dict[str, Any]:
            from src.loopie.reliability.evals import evaluate_suite

            state_key = "weaveEvalBaseline" if label == "baseline" else "weaveEvalPatched"
            try:
                result = evaluate_suite(
                    label=label,
                    redis=self.redis,
                    ledger=self.ledger,
                    correction_id=correction_id,
                    artifact_proof=artifact_proof or self.state.get("artifactProof"),
                    mode=self._llm_mode(),
                )
            except Exception as exc:
                result = {
                    "label": label,
                    "weave_eval_error": f"{type(exc).__name__}: {exc}",
                    "weave_project_url": None,
                }
            if not result.get("weave_eval_error") and not result.get("weave_project_url"):
                result["weave_eval_error"] = (
                    "Weave eval did not produce a dashboard URL. "
                    "Set WANDB_ENTITY and confirm the eval is visible in W&B."
                )
            self.state[state_key] = result
            return result

        return self._timed(f"weave_eval_{label}", _do)

    def propose_corrections(self) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            failure = self.state.get("currentFailure")
            if not failure:
                return {"error": "no_current_failure"}
            correction = agentic_diagnosis(failure)
            shadow = shadow_evaluate_correction(
                correction,
                tickets=tickets_by_id(),
                redis=self.redis,
                ledger=self.ledger,
            )
            correction = prepare_correction(
                correction,
                ledger=self.ledger,
                shadow_passed=bool(shadow["passed"]),
                shadow_eval_run_id=shadow["id"],
                shadow_result=shadow,
            )
            self.state["proposedCorrections"] = [correction]
            self.state["approvalState"] = "pending"
            return correction

        return self._timed("propose", _do)

    def approve_correction(self, correction_id: str) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            correction = next(
                (c for c in self.state.get("proposedCorrections", []) if c.get("id") == correction_id),
                None,
            )
            if correction is None:
                return {"error": "correction_not_found"}
            result = apply(correction, redis=self.redis, ledger=self.ledger)
            self.state["approvalState"] = "approved"
            self.state["artifactHistory"] = self.ledger.artifact_history(result["artifact_key"])
            self.state["artifactProof"] = {
                "correction_id": result.get("correction_id"),
                "before_hash": result.get("before_hash"),
                "after_hash": result.get("after_hash"),
                "diff": result.get("diff", []),
                "artifact_key": result.get("artifact_key"),
                "version": result.get("version"),
            }
            self.ledger.record_audit("approval", {"correction_id": correction_id, "result": result})
            return result

        return self._timed("approve", _do)

    def run_patched(self, *, case_id: str = "security_001") -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            ticket = tickets_by_id()[case_id]
            run = run_ticket(
                ticket,
                redis=self.redis,
                ledger=self.ledger,
                mode=self._llm_mode(),
                artifact_version="v2",
            )
            scores = score_run(run, ticket)
            passed = run_passed(scores)
            eval_run_id = f"patched_{uuid.uuid4().hex[:8]}"
            self.state["runs"][eval_run_id] = {
                "label": "patched",
                "case_id": case_id,
                "run": run,
                "scores": scores,
            }
            baseline = next((r for r in self.state["runs"].values() if r["label"] == "baseline"), None)
            self.state["evalDelta"] = {
                "case_id": case_id,
                "baseline_passed": baseline["scores"] if baseline else {},
                "patched_passed": scores,
                "improved": passed and baseline and not run_passed(baseline["scores"]),
            }
            self.redis.xadd("evals", {"event": "patched_complete", "case_id": case_id, "passed": passed})
            artifact_proof = self.state.get("artifactProof")
            return {
                "eval_run_id": eval_run_id,
                "passed": passed,
                "scores": scores,
                "evalDelta": self.state["evalDelta"],
                "run": run,
                "artifact_proof": artifact_proof,
            }

        payload = self._timed("patched", _do)
        artifact_proof = self.state.get("artifactProof")
        weave_eval = self._run_weave_eval_if_enabled(
            label="patched",
            correction_id=(artifact_proof or {}).get("correction_id"),
            artifact_proof=artifact_proof,
        )
        if weave_eval is not None:
            payload["weave_eval"] = weave_eval
        return payload

    def counterfactual_replay_suite(self, *, hero_case_id: str = "security_001") -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            ticket = tickets_by_id()[hero_case_id]
            neighbors = ticket.get("neighbors", [])
            result = counterfactual_replay(
                hero_case_id=hero_case_id,
                neighbor_case_ids=neighbors,
                run_case=lambda t: run_ticket(
                    t,
                    redis=self.redis,
                    ledger=self.ledger,
                    mode=self._llm_mode(),
                    artifact_version="v2",
                ),
                tickets_by_id=tickets_by_id(),
            )
            self.state["counterfactual"] = result
            return result

        return self._timed("counterfactual", _do)

    @staticmethod
    def _collect_live_fallback_cases(*runs: dict[str, Any] | None) -> list[str]:
        fallback_cases: list[str] = []
        for run in runs:
            if not run:
                continue
            case_id = run.get("case_id")
            if run.get("fallback_used"):
                fallback_cases.append(case_id)
        return fallback_cases

    @staticmethod
    def _collect_dishonest_live_cases(*runs: dict[str, Any] | None, tickets: dict[str, dict[str, Any]]) -> list[str]:
        dishonest: list[str] = []
        for run in runs:
            if not run:
                continue
            case_id = run.get("case_id", "")
            ticket = tickets.get(case_id, {"case_id": case_id})
            if not live_decision_honest(run, ticket):
                dishonest.append(case_id)
        return dishonest

    @staticmethod
    def _collect_oracle_mismatch_cases(*runs: dict[str, Any] | None, tickets: dict[str, dict[str, Any]]) -> list[str]:
        mismatches: list[str] = []
        for run in runs:
            if not run:
                continue
            case_id = run.get("case_id", "")
            ticket = tickets.get(case_id, {"case_id": case_id})
            if run.get("decided_by") == "llm" and not oracle_match(run, ticket):
                mismatches.append(case_id)
        return mismatches

    @staticmethod
    def _collect_incomplete_live_decisions(*runs: dict[str, Any] | None) -> list[str]:
        """Live rehearsal must complete an LLM call before cache hits count as honest."""
        incomplete: list[str] = []
        for run in runs:
            if not run:
                continue
            case_id = run.get("case_id", "")
            if run.get("decided_by") != "llm":
                continue
            if run.get("cache_hit"):
                continue
            if run.get("stop_reason") != "completed":
                incomplete.append(case_id)
        return incomplete

    def get_artifact_history(self, key: str) -> list[dict[str, Any]]:
        return self.ledger.artifact_history(key)

    def get_budget_status(self) -> dict[str, Any]:
        import os

        return {
            "ledger_total_cost": self.ledger.total_cost(),
            "test_total_cost": self.ledger.total_cost(mode="test"),
            "chat_cost_usd": self.ledger.total_cost(mode="chat"),
            "max_chat_cost_usd": float(os.getenv("LOOPIE_MAX_CHAT_COST_USD", "40")),
            "cost_by_provider": self.ledger.cost_by_provider(),
            "pipeline_budget": self.state.get("budget", {}),
        }

    def run_suite(self, *, mode: str = "test", reset: bool | None = None) -> dict[str, Any]:
        import os

        from src.loopie.config import normalize_llm_mode

        mode = normalize_llm_mode(mode)
        os.environ["LOOPIE_LLM_MODE"] = mode
        if mode == "live":
            os.environ.setdefault("LOOPIE_LIVE_CONFIRMED", "1")
        self._refresh_settings()

        should_reset = reset if reset is not None else mode == "live"
        if should_reset:
            self.reset()
        else:
            self.seed()

        baseline = self.run_baseline(case_id="security_001")
        eval_baseline = self.state.get("weaveEvalBaseline")
        if not baseline.get("failure"):
            return {"ok": False, "step": "baseline", "detail": baseline, "eval_baseline": eval_baseline}

        proposal = self.propose_corrections()
        approved = self.approve_correction(proposal["id"])
        patched = self.run_patched(case_id="security_001")
        eval_patched = self.state.get("weaveEvalPatched")
        counterfactual = self.counterfactual_replay_suite(hero_case_id="security_001")

        counterfactual_runs = [entry["run"] for entry in counterfactual.get("results", {}).values()]
        live_fallback_cases = self._collect_live_fallback_cases(
            baseline.get("failure", {}).get("run"),
            patched.get("run"),
            *counterfactual_runs,
        )
        eval_fallback_cases = [
            result["case_id"]
            for suite in (eval_baseline, eval_patched)
            for result in (suite or {}).get("results", [])
            if result.get("fallback_used")
        ]
        live_fallback_cases = sorted(set(live_fallback_cases + eval_fallback_cases))
        ticket_map = tickets_by_id()
        run_dicts = [
            baseline.get("failure", {}).get("run"),
            patched.get("run"),
            *counterfactual_runs,
        ]
        eval_rows = [
            row
            for suite in (eval_baseline, eval_patched)
            for row in (suite or {}).get("results", [])
        ]
        dishonest_cases = sorted(
            set(self._collect_dishonest_live_cases(*run_dicts, *eval_rows, tickets=ticket_map))
        )
        oracle_mismatch_cases = sorted(
            set(self._collect_oracle_mismatch_cases(*run_dicts, tickets=ticket_map))
        )
        incomplete_live_cases = sorted(
            set(self._collect_incomplete_live_decisions(*run_dicts, *eval_rows))
        )

        weave_errors = [
            err
            for err in (
                (eval_baseline or {}).get("weave_eval_error"),
                (eval_patched or {}).get("weave_eval_error"),
            )
            if err
        ]
        weave_manual_fallback = any(
            bool((result or {}).get("weave_eval_used_manual_fallback"))
            for result in (eval_baseline, eval_patched)
        )

        core_ok = patched["passed"] and counterfactual["no_regression"]
        weave_ok = True
        if get_settings().weave_enabled:
            weave_ok = len(weave_errors) == 0 and not weave_manual_fallback
        live_honest = (
            len(live_fallback_cases) == 0
            and len(dishonest_cases) == 0
            and len(oracle_mismatch_cases) == 0
            and len(incomplete_live_cases) == 0
        )
        ok = core_ok and weave_ok and (live_honest if mode == "live" else True)

        return {
            "ok": ok,
            "baseline": baseline,
            "proposal": proposal,
            "approved": approved,
            "patched": patched,
            "counterfactual": counterfactual,
            "eval_baseline": eval_baseline,
            "eval_patched": eval_patched,
            "live_fallback_cases": live_fallback_cases,
            "dishonest_live_cases": dishonest_cases,
            "oracle_mismatch_cases": oracle_mismatch_cases,
            "incomplete_live_cases": incomplete_live_cases,
            "weave_eval_errors": weave_errors,
            "weave_eval_used_manual_fallback": weave_manual_fallback,
            "budget": self.get_budget_status(),
            "mode": mode,
            "reset": should_reset,
        }

    def export_state(self) -> dict[str, Any]:
        try:
            self.state["events"] = (
                self.redis.xread_recent("evals")
                + self.redis.xread_recent("swarm")
                + self.redis.xread_recent("corrections")
            )
        except Exception as exc:
            self.state["events"] = self.state.get("events", [])
            self.state["eventStreamError"] = f"{type(exc).__name__}: {exc}"

        try:
            self.state["preflight"] = run_preflight(redis=self.redis, ledger=self.ledger)
        except Exception as exc:
            self.state["preflight"] = {
                "ok": False,
                "redis_reachable": False,
                "postgres_reachable": False,
                "weave_enabled": get_settings().weave_enabled,
                "weave_flag": get_settings().weave_enabled,
                "provider_mode": self._llm_mode(),
                "llm_mode": self._llm_mode(),
                "error": f"{type(exc).__name__}: {exc}",
            }

        try:
            self._refresh_export_budget()
        except Exception as exc:
            self.state["budgetError"] = f"{type(exc).__name__}: {exc}"
        return self.state
