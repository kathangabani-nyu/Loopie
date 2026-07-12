"""Single human review path for correction approval and rejection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from src.loopie.reliability.corrections import project_pending_outbox
from src.loopie.services.runs import RunService
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore

ApprovalChannel = Literal["hitl_chat", "rest", "ui"]


@dataclass
class ApprovalService:
    ledger: Ledger
    redis: RedisStore
    runs: RunService

    async def approve(
        self,
        correction_id: str,
        *,
        actor: str,
        channel: ApprovalChannel,
        note: str | None = None,
    ) -> dict[str, Any]:
        correction = await asyncio.to_thread(self.ledger.get_correction, correction_id)
        if correction is None:
            raise KeyError(f"Unknown correction {correction_id}")
        committed = await asyncio.to_thread(
            self.ledger.commit_correction,
            correction_id,
            actor=actor,
            channel=channel,
            note=note,
        )
        projected = await asyncio.to_thread(
            project_pending_outbox,
            ledger=self.ledger,
            redis=self.redis,
        )

        patched_run: dict[str, Any] | None = None
        failure_id = correction.get("failure_id")
        if failure_id:
            failure = await self.runs.repository.get_failure(str(failure_id))
            if failure is not None:
                parent = await self.runs.repository.get_run(str(failure["run_id"]))
                parent_manifest = (
                    await self.runs.repository.get_run_manifest(str(parent["manifest_id"]))
                    if parent is not None and parent.get("manifest_id")
                    else None
                )
                queued = await self.runs.queue_ticket_run(
                    ticket_id=str(failure["ticket_id"]),
                    mode=str(failure["mode"]),
                    kind="patched",
                    idempotency_key=f"correction:{correction_id}:patched",
                    parent_run_id=str(failure["run_id"]),
                    correction_id=correction_id,
                    ticket_snapshot=(parent_manifest.ticket_snapshot if parent_manifest else None),
                    evaluation_snapshot=(parent_manifest.evaluation_snapshot if parent_manifest else None),
                )
                patched_run = {
                    "run_id": queued["run"]["id"],
                    "job_id": queued["job"]["id"],
                    "status": queued["run"]["status"],
                    "parent_run_id": failure["run_id"],
                    "correction_id": correction_id,
                }

        result = {**committed, "projected": projected, "patched_run": patched_run}
        self.runs.emit_event(
            "correction.approved",
            {"correction_id": correction_id, "patched_run": patched_run},
        )
        return result

    async def reject(
        self,
        correction_id: str,
        *,
        actor: str,
        channel: ApprovalChannel,
        note: str | None = None,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.ledger.reject_correction,
            correction_id,
            actor=actor,
            channel=channel,
            note=note,
        )
        self.runs.emit_event(
            "correction.rejected",
            {"correction_id": correction_id, "status": "rejected"},
        )
        return result
