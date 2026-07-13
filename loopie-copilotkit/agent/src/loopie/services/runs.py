"""Pinned-manifest run scheduling and execution service."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from src.loopie.config import get_settings, normalize_llm_mode
from src.loopie.jobs import Job, MemoryJobStore, PostgresJobStore
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION
from src.loopie.manifests import ManifestReader, build_run_manifest
from src.loopie.native_evals import (
    compact_evaluation_output,
    create_native_evaluation,
    evaluation_row,
    flatten_correctness,
)
from src.loopie.product_repository import ProductRepository
from src.loopie.runner import run_ticket
from src.loopie.reliability.scorers import score_layers
from src.loopie.reliability.classifier import classify_production_failure
from src.loopie.reliability.judge import AdvisoryJudge
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore

JobStore = MemoryJobStore | PostgresJobStore
logger = logging.getLogger(__name__)


class RunService:
    def __init__(self, *, repository: ProductRepository, jobs: JobStore, redis: RedisStore, ledger: Ledger, judge: AdvisoryJudge | None = None) -> None:
        self.repository = repository
        self.jobs = jobs
        self.redis = redis
        self.ledger = ledger
        self.judge = judge

    def emit_event(self, event: str, data: dict[str, Any]) -> None:
        try:
            self.redis.xadd("product", {"event": event, "data": data})
        except Exception:
            logger.exception("Redis event publication failed for %s", event)

    async def queue_ticket_run(
        self,
        *,
        ticket_id: str,
        mode: str,
        idempotency_key: str,
        kind: str = "ticket",
        parent_run_id: str | None = None,
        correction_id: str | None = None,
        ticket_snapshot: dict[str, Any] | None = None,
        evaluation_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticket = await self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(f"Unknown ticket {ticket_id}")
        mode = normalize_llm_mode(mode)
        settings = get_settings()
        if evaluation_snapshot is None and kind == "golden":
            evaluation_snapshot = await self.repository.get_golden_annotation(
                ticket_id,
                project_id=str(ticket["project_id"]),
            )
            if evaluation_snapshot is None:
                raise RuntimeError("Golden run references a ticket without a golden annotation")
        manifest = await asyncio.to_thread(
            build_run_manifest,
            self.redis,
            ticket_snapshot or ticket,
            project_id=str(ticket["project_id"]),
            prompt_version=DECISION_PROMPT_VERSION,
            schema_version=DECISION_SCHEMA_VERSION,
            model_version=settings.openai_model,
            evaluation_snapshot=evaluation_snapshot,
        )
        run, _ = await self.repository.queue_run(
            ticket=ticket,
            manifest=manifest,
            mode=mode,
            kind=kind,
            idempotency_key=idempotency_key,
            parent_run_id=parent_run_id,
            correction_id=correction_id,
        )
        job = await self.jobs.enqueue(
            job_type="execute_run",
            payload={"run_id": str(run["id"])},
            idempotency_key=f"run:{run['id']}",
        )
        self.emit_event(
            "run.queued",
            {"run_id": str(run["id"]), "job_id": str(job.id), "status": "queued"},
        )
        return {"run": run, "job": asdict(job)}

    async def execute(self, job: Job) -> dict[str, Any]:
        if job.job_type != "execute_run":
            raise ValueError(f"Unsupported job type {job.job_type}")
        run_id = str(job.payload["run_id"])
        run = await self.repository.get_run(run_id, project_id=job.project_id)
        if run is None:
            raise KeyError(f"Unknown run {run_id}")
        manifest = await self.repository.get_run_manifest(str(run["manifest_id"]), project_id=job.project_id)
        if manifest is None:
            raise RuntimeError("Run references a missing manifest")

        await self.repository.mark_run_running(run_id)
        self.emit_event("run.running", {"run_id": run_id, "status": "running"})
        agent_ticket = ManifestReader(manifest).ticket_input()
        golden_annotation = manifest.evaluation_snapshot
        if str(run["kind"]) == "golden" and golden_annotation is None:
            raise RuntimeError("Golden run manifest has no pinned annotation")
        if golden_annotation is not None:
            golden_annotation = {
                **golden_annotation,
                **dict(golden_annotation.get("expected_metadata") or {}),
                "neighbors": list(golden_annotation.get("declared_neighbors") or []),
            }
            if str(run["mode"]) == "test":
                agent_ticket.update(
                    {
                        key: golden_annotation[key]
                        for key in (
                            "expected_action",
                            "failure_seed",
                            "expected_memory_version",
                            "must_check_policy_version",
                            "neighbors",
                        )
                        if golden_annotation.get(key) is not None
                    }
                )
        run_kind = str(run["kind"])
        evaluation_phase = "baseline" if run_kind == "golden" else "applied_patch"
        artifact_label = "v1" if run_kind == "golden" else "v2"
        evaluation_ticket = {**agent_ticket, **(golden_annotation or {})}
        native_evaluation = create_native_evaluation(
            name=f"Loopie Golden {evaluation_phase} {artifact_label} · {run_id[:8]}",
            dataset_name="loopie_golden_demo",
            dataset_rows=[evaluation_row(evaluation_ticket)],
            model=(
                f"{get_settings().openai_model}:{artifact_label}:"
                f"{manifest.content_hash[:16]}"
            ),
            attributes={
                "compare_group": "loopie_golden_demo",
                "iteration": evaluation_phase,
                "artifact_version": artifact_label,
                "artifact_hash": manifest.content_hash,
                "run_id": run_id,
                "correction_id": (
                    str(run["correction_id"]) if run.get("correction_id") else None
                ),
            },
            enabled=(
                run_kind in {"golden", "patched"} and str(run["mode"]) == "live"
            ),
        )
        with native_evaluation.prediction(evaluation_row(evaluation_ticket)) as prediction:
            result = await asyncio.to_thread(
                run_ticket,
                agent_ticket,
                redis=self.redis,
                ledger=self.ledger,
                mode=str(run["mode"]),
                artifact_version=manifest.content_hash[:16],
                phase="baseline" if run_kind == "golden" else run_kind,
                correction_id=(
                    str(run["correction_id"]) if run.get("correction_id") else None
                ),
                parent_run_id=(
                    str(run["parent_run_id"]) if run.get("parent_run_id") else None
                ),
                manifest=manifest,
                run_id=run_id,
                project_id=job.project_id,
            )
            result["correctness"] = score_layers(
                result,
                agent_ticket,
                golden_annotation=golden_annotation,
            )
            native_evaluation.record(
                prediction,
                output=compact_evaluation_output(result),
                scores=flatten_correctness(result["correctness"]),
            )
        evaluation_evidence = native_evaluation.finish(
            {
                "passed": bool(result["correctness"]["passed"]),
                "run_id": run_id,
                "phase": evaluation_phase,
                "artifact_version": artifact_label,
            }
        )
        if evaluation_evidence["status"] != "disabled":
            result["weave_evaluation"] = evaluation_evidence
        if not result["correctness"]["passed"]:
            classification = await classify_production_failure(
                ticket=agent_ticket,
                run=result,
                correctness=result["correctness"],
                test_lane=str(run["kind"]) == "golden" and str(run["mode"]) == "test",
            )
            result["failure_category"] = classification.category
            result["failure_classification"] = classification.model_dump(mode="json")
        await self.repository.finish_run(
            run_id,
            result,
            job_id=job.id,
            lease_token=job.lease_token,
        )
        if isinstance(self.jobs, MemoryJobStore) and job.lease_token:
            completed = await self.jobs.complete(job_id=job.id, lease_token=job.lease_token)
            if not completed:
                raise RuntimeError("Job lease was lost before in-memory finalization")
        self.emit_event(
            "run.finished",
            {
                "run_id": run_id,
                "status": "succeeded",
                "passed": bool(result["correctness"]["passed"]),
                "improvement_proof": result.get("improvement_proof"),
            },
        )
        if self.judge is not None:
            try:
                verdict = await self.judge.review(ticket=agent_ticket, run=result)
                if verdict is not None:
                    calibration = await self.repository.judge_calibration(project_id=job.project_id)
                    calibration_sample = str(run["kind"]) == "golden"
                    if calibration_sample or (
                        calibration["calibrated"] and verdict["verdict"] == "flag"
                    ):
                        await self.repository.create_triage_item(
                            run_id=run_id,
                            verdict=verdict,
                            confidence=float(verdict["confidence"]),
                            calibration_sample=calibration_sample,
                            project_id=job.project_id,
                        )
            except Exception:
                # Judge availability cannot change authoritative run status.
                logger.exception("Advisory judge failed for run %s", run_id)
        return result
