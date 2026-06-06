"""End-to-end Loopie pipeline orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from src.loopie.config import get_settings
from src.loopie.reliability.classifier import classify_failure
from src.loopie.reliability.corrections import apply, propose
from src.loopie.reliability.replay import counterfactual_replay
from src.loopie.reliability.scorers import run_passed, score_run
from src.loopie.runner import load_tickets, run_ticket, seed_baseline, tickets_by_id
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


class LoopiePipeline:
    def __init__(self) -> None:
        self.redis = RedisStore()
        self.ledger = Ledger.connect()
        self.settings = get_settings()
        self.state: dict[str, Any] = {
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

    def seed(self) -> dict[str, Any]:
        result = seed_baseline(redis=self.redis, ledger=self.ledger)
        self.state["events"] = self.redis.xread_recent("evals")
        return result

    def run_baseline(self, *, case_id: str = "security_001") -> dict[str, Any]:
        ticket = tickets_by_id()[case_id]
        run = run_ticket(ticket, redis=self.redis, ledger=self.ledger, mode=self.settings.llm_mode)
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
        run = run_ticket(ticket, redis=self.redis, ledger=self.ledger, mode=self.settings.llm_mode, artifact_version="v2")
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
        return {"eval_run_id": eval_run_id, "passed": passed, "scores": scores, "evalDelta": self.state["evalDelta"]}

    def counterfactual_replay_suite(self, *, hero_case_id: str = "security_001") -> dict[str, Any]:
        ticket = tickets_by_id()[hero_case_id]
        neighbors = ticket.get("neighbors", [])
        result = counterfactual_replay(
            hero_case_id=hero_case_id,
            neighbor_case_ids=neighbors,
            run_case=lambda t: run_ticket(t, redis=self.redis, ledger=self.ledger, mode=self.settings.llm_mode, artifact_version="v2"),
            tickets_by_id=tickets_by_id(),
        )
        self.state["counterfactual"] = result
        return result

    def get_artifact_history(self, key: str) -> list[dict[str, Any]]:
        return self.ledger.artifact_history(key)

    def get_budget_status(self) -> dict[str, Any]:
        return {
            "ledger_total_cost": self.ledger.total_cost(),
            "mock_total_cost": self.ledger.total_cost(mode="mock"),
            "pipeline_budget": self.state.get("budget", {}),
        }

    def run_suite(self, *, mode: str = "mock") -> dict[str, Any]:
        import os

        os.environ["LOOPIE_LLM_MODE"] = mode
        from src.loopie.config import get_settings

        get_settings.cache_clear()
        self.seed()
        baseline = self.run_baseline(case_id="security_001")
        if not baseline.get("failure"):
            return {"ok": False, "step": "baseline", "detail": baseline}
        proposal = self.propose_corrections()
        approved = self.approve_correction(proposal["id"])
        patched = self.run_patched(case_id="security_001")
        counterfactual = self.counterfactual_replay_suite(hero_case_id="security_001")
        return {
            "ok": patched["passed"] and counterfactual["no_regression"],
            "baseline": baseline,
            "proposal": proposal,
            "approved": approved,
            "patched": patched,
            "counterfactual": counterfactual,
            "budget": self.get_budget_status(),
            "mode": mode,
        }

    def export_state(self) -> dict[str, Any]:
        self.state["events"] = self.redis.xread_recent("swarm") + self.redis.xread_recent("corrections")
        return self.state
