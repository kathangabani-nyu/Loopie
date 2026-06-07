"""End-to-end Loopie pipeline orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from src.loopie.config import get_settings
from src.loopie.reliability.classifier import classify_failure
from src.loopie.reliability.corrections import apply
from src.loopie.reliability.diagnosis import agentic_diagnosis
from src.loopie.reliability.scorers import live_decision_honest, oracle_match
from src.loopie.reliability.replay import counterfactual_replay
from src.loopie.reliability.scorers import run_passed, score_run
from src.loopie.runner import LIVE_DECISION_CASES, load_tickets, run_ticket, seed_baseline, tickets_by_id
from src.loopie.preflight import run_preflight
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


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
            "approvalState": "idle",
        }

    def reset(self) -> dict[str, Any]:
        """Wipe Redis + ledger back to a clean slate and reseed baseline artifacts."""
        self.redis.flush_loopie_keys()
        self.ledger.reset()
        self.state = self._initial_state()
        seeded = self.seed()
        return {"reset": True, **seeded}

    def seed(self) -> dict[str, Any]:
        result = seed_baseline(redis=self.redis, ledger=self.ledger)
        self.state["events"] = self.redis.xread_recent("evals")
        return result

    def run_baseline(self, *, case_id: str = "security_001") -> dict[str, Any]:
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
        self.state["runs"][eval_run_id] = {"label": "baseline", "case_id": case_id, "run": run, "scores": scores}
        self.state["budget"] = run.get("budget", {})
        self.redis.xadd("evals", {"event": "baseline_complete", "case_id": case_id, "passed": passed})
        weave_eval = None
        if not passed:
            weave_eval = self._run_weave_eval_if_enabled(label="baseline")
        payload = {"eval_run_id": eval_run_id, "passed": passed, "scores": scores, "failure": failure}
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
        from src.loopie.reliability.evals import evaluate_suite

        result = evaluate_suite(
            label=label,
            redis=self.redis,
            ledger=self.ledger,
            correction_id=correction_id,
            artifact_proof=artifact_proof or self.state.get("artifactProof"),
            mode=self._llm_mode(),
        )
        state_key = "weaveEvalBaseline" if label == "baseline" else "weaveEvalPatched"
        self.state[state_key] = result
        return result

    def propose_corrections(self) -> dict[str, Any]:
        failure = self.state.get("currentFailure")
        if not failure:
            return {"error": "no_current_failure"}
        correction = agentic_diagnosis(failure)
        self.state["proposedCorrections"] = [correction]
        self.state["approvalState"] = "pending"
        return correction

    def approve_correction(self, correction_id: str) -> dict[str, Any]:
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

    def run_patched(self, *, case_id: str = "security_001") -> dict[str, Any]:
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
        self.state["runs"][eval_run_id] = {"label": "patched", "case_id": case_id, "run": run, "scores": scores}
        baseline = next((r for r in self.state["runs"].values() if r["label"] == "baseline"), None)
        self.state["evalDelta"] = {
            "case_id": case_id,
            "baseline_passed": baseline["scores"] if baseline else {},
            "patched_passed": scores,
            "improved": passed and baseline and not run_passed(baseline["scores"]),
        }
        self.redis.xadd("evals", {"event": "patched_complete", "case_id": case_id, "passed": passed})
        artifact_proof = self.state.get("artifactProof")
        weave_eval = self._run_weave_eval_if_enabled(
            label="patched",
            correction_id=(artifact_proof or {}).get("correction_id"),
            artifact_proof=artifact_proof,
        )
        payload = {
            "eval_run_id": eval_run_id,
            "passed": passed,
            "scores": scores,
            "evalDelta": self.state["evalDelta"],
            "run": run,
            "artifact_proof": artifact_proof,
        }
        if weave_eval is not None:
            payload["weave_eval"] = weave_eval
        return payload

    def counterfactual_replay_suite(self, *, hero_case_id: str = "security_001") -> dict[str, Any]:
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
            if case_id not in LIVE_DECISION_CASES:
                continue
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
            "mock_total_cost": self.ledger.total_cost(mode="mock"),
            "chat_cost_usd": self.ledger.total_cost(mode="chat"),
            "max_chat_cost_usd": float(os.getenv("LOOPIE_MAX_CHAT_COST_USD", "40")),
            "cost_by_provider": self.ledger.cost_by_provider(),
            "pipeline_budget": self.state.get("budget", {}),
        }

    def run_suite(self, *, mode: str = "mock", reset: bool | None = None) -> dict[str, Any]:
        import os

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
            if result.get("case_id") in LIVE_DECISION_CASES and result.get("fallback_used")
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
        import os

        self.state["events"] = (
            self.redis.xread_recent("evals")
            + self.redis.xread_recent("swarm")
            + self.redis.xread_recent("corrections")
        )
        self.state["preflight"] = run_preflight(redis=self.redis, ledger=self.ledger)
        budget = dict(self.state.get("budget") or {})
        budget["chat_cost_usd"] = self.ledger.total_cost(mode="chat")
        budget["max_chat_cost_usd"] = float(os.getenv("LOOPIE_MAX_CHAT_COST_USD", "40"))
        self.state["budget"] = budget
        return self.state
