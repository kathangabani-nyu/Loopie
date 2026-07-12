"""Correction proposal, blast-radius, and shadow-evaluation service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from src.loopie.product_repository import ProductRepository, ticket_to_agent_input
from src.loopie.policy.compiler import compile_policy
from src.loopie.policy.dsl import evaluate_policy
from src.loopie.reliability.correction_gen import generate_correction
from src.loopie.reliability.corrections import prepare_correction, propose, shadow_evaluate_correction
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


@dataclass
class CorrectionService:
    repository: ProductRepository
    redis: RedisStore
    ledger: Ledger

    async def propose_for_failure(self, failure_id: str) -> dict[str, Any]:
        failure = await self.repository.get_failure(failure_id)
        if failure is None:
            raise KeyError(f"Unknown failure {failure_id}")

        category = str(failure["category"])
        diagnosis = failure.get("diagnosis") or failure.get("scores") or {}
        if diagnosis.get("execution", {}).get("passed") is False:
            raise ValueError(
                "operational execution failures require provider/store remediation, not an artifact correction"
            )
        if category == "policy_violation" and str(failure["mode"]) == "test":
            violated = [
                rule.get("rule_id")
                for rule in diagnosis.get("policy", {}).get("rules", [])
                if not rule.get("passed", True)
            ]
            if "security_flag_blocks_refund" in violated:
                category = "missing_guard"
            elif "refund_window_30_days" in violated:
                category = "stale_memory"
            else:
                raise ValueError("no test fixture correction exists for these violated rules")

        if str(failure["mode"]) == "test":
            correction = propose(category, case_id=str(failure["external_id"]))
            correction["failure_id"] = failure_id
        else:
            project = await self.repository.get_project()
            if project is None:
                raise RuntimeError("default project is not seeded")
            history_keys = [
                "policy:rules",
                "routing:rules",
                "memory:policy:refund_window",
                "memory:policy:vat_reverse_charge",
                "config:max_transitions",
            ]
            histories = await asyncio.gather(
                *(asyncio.to_thread(self.ledger.artifact_history, key) for key in history_keys)
            )
            correction = await generate_correction(
                failure=failure,
                artifact_history=dict(zip(history_keys, histories, strict=True)),
                read_set=list((failure.get("decision") or {}).get("read_set") or []),
                action_taxonomy=list(project["action_taxonomy"]),
            )

        artifact_key = {
            "policy_rule": "policy:rules",
            "routing_rule": "routing:rules",
            "memory_update": f"memory:{correction['proposal'].get('key', '')}",
            "config_update": f"config:{correction['proposal'].get('key', '')}",
        }.get(str(correction["type"]))
        if not artifact_key:
            raise ValueError("correction has no projectable artifact")

        pinned_ticket = (
            ((failure.get("decision") or {}).get("run_manifest") or {}).get("ticket_snapshot")
            or failure
        )
        primary = ticket_to_agent_input(pinned_ticket)
        affected_rows = await self.repository.tickets_affected_by_artifact(artifact_key, limit=100)
        holdout_rows = await self.repository.list_tickets(limit=6)
        tickets = {str(primary["case_id"]): primary}
        for row in [*affected_rows, *holdout_rows]:
            candidate = ticket_to_agent_input(row)
            tickets[str(candidate["case_id"])] = candidate
        correction["blast_radius"] = {
            "artifact_key": artifact_key,
            "ticket_ids": [str(row["id"]) for row in affected_rows],
            "source": "run_read_sets_intersection",
        }
        shadow = await asyncio.to_thread(
            shadow_evaluate_correction,
            correction,
            tickets=tickets,
            redis=self.redis,
            ledger=self.ledger,
            mode=str(failure["mode"]),
        )
        return await asyncio.to_thread(
            prepare_correction,
            correction,
            ledger=self.ledger,
            shadow_passed=bool(shadow["passed"]),
            shadow_eval_run_id=shadow["id"],
            shadow_result=shadow,
        )

    async def compile_policy(
        self,
        *,
        source_text: str,
        source_doc_ref: str,
    ) -> dict[str, Any]:
        project = await self.repository.get_project()
        if project is None:
            raise RuntimeError("default project is not seeded")
        compiled, model = await compile_policy(
            source_text=source_text,
            source_doc_ref=source_doc_ref,
            action_taxonomy=list(project["action_taxonomy"]),
        )
        rule = compiled.rule.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(rule, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        correction = {
            "id": f"policy_{digest[:16]}",
            "case_id": f"policy:{rule['rule_id']}",
            "category": "policy_authoring",
            "type": "policy_rule",
            "proposal": rule,
            "summary": f"Compile policy rule {rule['rule_id']} from {source_doc_ref}.",
            "rationale": compiled.rationale,
            "source_doc_ref": source_doc_ref,
            "proposed_by": "llm",
            "model": model,
            "idempotency_key": f"policy:{source_doc_ref}:{digest}",
            "blast_radius": {"artifact_key": "policy:rules", "source": "policy_dry_run"},
        }

        dry_run: list[dict[str, Any]] = []
        for run in await self.repository.list_runs(limit=100):
            decision = run.get("decision") or {}
            if not decision:
                continue
            ticket = await self.repository.get_ticket(str(run["ticket_id"]))
            if ticket is None:
                continue
            evaluation = evaluate_policy(
                compiled.rule,
                {
                    "ticket": ticket_to_agent_input(ticket),
                    "context": {"mode": run.get("mode")},
                    "artifacts": decision.get("artifacts_snapshot") or {},
                    "decision": {
                        "action": decision.get("action"),
                        "tool_calls": [
                            call.get("name") for call in decision.get("tool_calls", [])
                        ],
                    },
                },
            )
            dry_run.append(
                {
                    "run_id": str(run["id"]),
                    "ticket_id": str(run["ticket_id"]),
                    "applies": evaluation.applies,
                    "passed": evaluation.passed,
                    "violations": [violation.message for violation in evaluation.violations],
                    "read_set": list(evaluation.read_set),
                }
            )
        correction["blast_radius"]["ticket_ids"] = sorted(
            {row["ticket_id"] for row in dry_run if row["applies"]}
        )
        shadow = {
            "id": f"policy-dry-run-{digest[:12]}",
            "passed": True,
            "kind": "policy_compilation_dry_run",
            "results": dry_run,
            "note": "Passing means schema validation and deterministic dry-run completed; human policy intent remains authoritative.",
        }
        return await asyncio.to_thread(
            prepare_correction,
            correction,
            ledger=self.ledger,
            shadow_passed=True,
            shadow_eval_run_id=shadow["id"],
            shadow_result=shadow,
        )
