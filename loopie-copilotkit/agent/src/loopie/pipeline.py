"""End-to-end Loopie pipeline orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from src.loopie.config import get_settings
from src.loopie.reliability.classifier import classify_failure
from src.loopie.reliability.corrections import apply, propose
from src.loopie.reliability.replay import counterfactual_replay
from src.loopie.reliability.scorers import run_passed, score_run
from src.loopie.runner import LIVE_DECISION_CASES, load_tickets, run_ticket, seed_baseline, tickets_by_id
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


class LoopiePipeline:
    def __init__(self) -> None:
        self.redis = RedisStore()
        self.ledger = Ledger.connect()
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
        eval_run_id = f"baseline_{uuid.uuid4().hex[:8]}"
        self.state["runs"][eval_run_id] = {"label": "baseline", "case_id": case_id, "run": run, "scores": scores}
        self.state["budget"] = run.get("budget", {})
        self.redis.xadd("evals", {"event": "baseline_complete", "case_id": case_id, "passed": passed})
        return {"eval_run_id": eval_run_id, "passed": passed, "scores": scores, "failure": failure}

    def propose_corrections(self) -> dict[str, Any]:
        failure = self.state.get("currentFailure")
        if not failure:
            return {"error": "no_current_failure"}
        correction = propose(failure["category"], case_id=failure["case_id"])
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
        return {"eval_run_id": eval_run_id, "passed": passed, "scores": scores, "evalDelta": self.state["evalDelta"], "run": run}

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
            if case_id in LIVE_DECISION_CASES and run.get("fallback_used"):
                fallback_cases.append(case_id)
        return fallback_cases

    def get_artifact_history(self, key: str) -> list[dict[str, Any]]:
        return self.ledger.artifact_history(key)

    def get_budget_status(self) -> dict[str, Any]:
        return {
            "ledger_total_cost": self.ledger.total_cost(),
            "mock_total_cost": self.ledger.total_cost(mode="mock"),
            "pipeline_budget": self.state.get("budget", {}),
        }

    def run_suite(self, *, mode: str = "mock", reset: bool | None = None) -> dict[str, Any]:
        import os

        os.environ["LOOPIE_LLM_MODE"] = mode
        if mode == "live":
            os.environ.setdefault("LOOPIE_LIVE_CONFIRMED", "1")
        from src.loopie.reliability.evals import evaluate_suite

        self._refresh_settings()

        should_reset = reset if reset is not None else mode == "live"
        if should_reset:
            self.reset()
        else:
            self.seed()

        eval_baseline: dict[str, Any] | None = None
        eval_patched: dict[str, Any] | None = None
        if mode == "live":
            eval_baseline = evaluate_suite(
                label="baseline",
                redis=self.redis,
                ledger=self.ledger,
                mode=mode,
            )

        baseline = self.run_baseline(case_id="security_001")
        if not baseline.get("failure"):
            return {"ok": False, "step": "baseline", "detail": baseline, "eval_baseline": eval_baseline}

        proposal = self.propose_corrections()
        approved = self.approve_correction(proposal["id"])
        patched = self.run_patched(case_id="security_001")
        counterfactual = self.counterfactual_replay_suite(hero_case_id="security_001")

        if mode == "live":
            eval_patched = evaluate_suite(
                label="patched",
                redis=self.redis,
                ledger=self.ledger,
                correction_id=proposal.get("id"),
                mode=mode,
            )

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
        live_honest = len(live_fallback_cases) == 0 and len(weave_errors) == 0 and not weave_manual_fallback
        ok = core_ok and (live_honest if mode == "live" else True)

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
            "weave_eval_errors": weave_errors,
            "weave_eval_used_manual_fallback": weave_manual_fallback,
            "budget": self.get_budget_status(),
            "mode": mode,
            "reset": should_reset,
        }

    def export_state(self) -> dict[str, Any]:
        self.state["events"] = (
            self.redis.xread_recent("evals")
            + self.redis.xread_recent("swarm")
            + self.redis.xread_recent("corrections")
        )
        return self.state
