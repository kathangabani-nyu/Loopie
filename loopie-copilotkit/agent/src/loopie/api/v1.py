"""Ticket and durable-run HTTP surface."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.loopie.product_repository import FORBIDDEN_LIVE_TICKET_KEYS
from src.loopie.reliability.correction_gen import CorrectionGenerationUnavailable
from src.loopie.policy.compiler import PolicyCompilationUnavailable
from src.loopie.runtime import RuntimeServices
from src.loopie.preflight import run_preflight
from src.loopie.runner import seed_baseline

router = APIRouter(prefix="/api/v1")


def _runtime(request: Request) -> RuntimeServices:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="application runtime is not ready")
    return runtime


class DemoStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal["START_DEMO"]


class DemoReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal["RESET_DEMO"]


async def _demo_runs(runtime: RuntimeServices) -> list[dict[str, Any]]:
    return await runtime.repository.list_runs(limit=500)


async def _demo_ticket(runtime: RuntimeServices) -> dict[str, Any]:
    ticket = next(
        (
            item for item in await runtime.repository.list_tickets(limit=500)
            if item.get("external_id") == "security_001"
        ),
        None,
    )
    if ticket is None:
        raise HTTPException(status_code=503, detail="security_001 fixture is not seeded")
    return ticket


def _reject_active_demo_runs(runs: list[dict[str, Any]]) -> None:
    if any(run.get("status") in {"queued", "running"} for run in runs):
        raise HTTPException(status_code=409, detail="wait for active runs before changing the demo")


@router.post("/demo/reset")
async def reset_golden_demo(body: DemoReset, request: Request) -> Any:
    runtime = _runtime(request)
    runs = await _demo_runs(runtime)
    _reject_active_demo_runs(runs)
    await _demo_ticket(runtime)
    await asyncio.to_thread(runtime.stores.redis.flush_loopie_keys)
    await asyncio.to_thread(runtime.stores.ledger.reset)
    await runtime.repository.reset_demo_state()
    await asyncio.to_thread(seed_baseline, redis=runtime.stores.redis, ledger=runtime.stores.ledger)
    return {
        "reset": True,
        "scenario": "security_001",
        "status": "ready",
    }


@router.post("/demo/start", status_code=status.HTTP_202_ACCEPTED)
async def start_golden_demo(body: DemoStart, request: Request) -> Any:
    runtime = _runtime(request)
    readiness = await asyncio.to_thread(
        run_preflight,
        redis=runtime.stores.redis,
        ledger=runtime.stores.ledger,
    )
    if not readiness.get("weave_dashboard_ready"):
        raise HTTPException(status_code=503, detail="Golden Demo requires a configured Weave dashboard")
    if readiness.get("llm_mode") != "live" or not readiness.get("provider_ready"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Golden Demo requires live model execution: set LOOPIE_LLM_MODE=live, "
                "LOOPIE_LIVE_CONFIRMED=1, and configure an enabled provider"
            ),
        )
    runs = await _demo_runs(runtime)
    _reject_active_demo_runs(runs)
    if any(run.get("kind") in {"golden", "patched"} for run in runs):
        raise HTTPException(status_code=409, detail="reset the demo before starting a new baseline")
    ticket = await _demo_ticket(runtime)
    queued = await runtime.runs.queue_ticket_run(
        ticket_id=str(ticket["id"]),
        mode="live",
        kind="golden",
        idempotency_key="demo:security_001:live:baseline",
    )
    return jsonable_encoder(
        {
            "reset": True,
            "scenario": "security_001",
            "run_id": queued["run"]["id"],
            "status": queued["run"]["status"],
            "mode": "live",
        }
    )


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)
    channel: str = Field(default="api", min_length=1, max_length=50)
    customer_ref: str | None = Field(default=None, max_length=500)
    customer_tier: Literal["enterprise", "standard", "trial"]
    days_since_purchase: int = Field(ge=0, le=36500)
    security_flag: bool
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    amount_source: Literal["explicit", "missing"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=50)
    auto_evaluate: bool = True

    @field_validator("metadata")
    @classmethod
    def reject_golden_labels(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = FORBIDDEN_LIVE_TICKET_KEYS | {
            "case_id", "version", "request", "customer_tier", "days_since_purchase",
            "security_flag", "amount_minor", "currency", "amount_source", "amount",
        }
        leaked = sorted(reserved.intersection(value))
        if leaked:
            raise ValueError(f"golden-only fields are not valid live ticket metadata: {', '.join(leaked)}")
        return value

    @model_validator(mode="after")
    def normalize_amount_source(self) -> "TicketCreate":
        expected = "explicit" if self.amount_minor is not None else "missing"
        if self.amount_source is not None and self.amount_source != expected:
            raise ValueError(f"amount_source must be {expected} for the supplied amount")
        self.amount_source = expected
        return self


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["test", "live"] = "live"
    kind: Literal["ticket", "golden", "shadow", "counterfactual"] = "ticket"


class TriageResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirm", "reject"]
    actor: str = Field(min_length=1, max_length=200)
    expected_action: str | None = Field(default=None, max_length=64)


class CorrectionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="owner", min_length=1, max_length=200)
    channel: Literal["hitl_chat", "rest", "ui"] = "rest"
    note: str | None = Field(default=None, max_length=2_000)


class PolicyCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_text: str = Field(min_length=10, max_length=100_000)
    source_doc_ref: str = Field(min_length=1, max_length=500)


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    project = await _runtime(request).repository.get_project()
    if project is None:
        raise HTTPException(status_code=503, detail="default project is not seeded")
    return {
        "project": project,
        "failure_layers": ["policy", "structural", "golden"],
        "judge_role": "advisory_triage_only",
    }


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket(body: TicketCreate, request: Request) -> Any:
    ticket = await _runtime(request).repository.create_ticket(
        external_id=body.external_id,
        subject=body.subject,
        body=body.body,
        channel=body.channel,
        customer_ref=body.customer_ref,
        facts={
            "customer_tier": body.customer_tier,
            "days_since_purchase": body.days_since_purchase,
            "security_flag": body.security_flag,
            "amount_minor": body.amount_minor,
            "currency": body.currency,
            "amount_source": body.amount_source,
        },
        metadata=body.metadata,
        tags=body.tags,
    )
    queued = None
    if body.auto_evaluate:
        queued = await _runtime(request).runs.queue_ticket_run(
            ticket_id=str(ticket["id"]),
            mode="live",
            kind="ticket",
            idempotency_key=f"ingest:{ticket['id']}:v{ticket['version']}",
        )
    _runtime(request).runs.emit_event(
        "ticket.ingested",
        {"ticket_id": str(ticket["id"]), "run_id": str(queued["run"]["id"]) if queued else None},
    )
    return jsonable_encoder(
        {
            **ticket,
            "queued_run": (
                {"run_id": queued["run"]["id"], "job_id": queued["job"]["id"]}
                if queued
                else None
            ),
        }
    )


class TicketImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tickets: list[TicketCreate] | None = Field(default=None, max_length=500)
    format: Literal["csv", "jsonl"] | None = None
    content: str | None = Field(default=None, max_length=5_000_000)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "TicketImport":
        has_tickets = self.tickets is not None
        has_document = self.format is not None or self.content is not None
        if has_tickets == has_document:
            raise ValueError("provide either tickets or a format/content document")
        if has_document and (not self.format or not self.content):
            raise ValueError("format and content are both required")
        if has_tickets and not self.tickets:
            raise ValueError("tickets must not be empty")
        return self


def _parse_ticket_document(body: TicketImport) -> list[TicketCreate]:
    if body.tickets is not None:
        return body.tickets
    assert body.format and body.content
    raw_rows: list[dict[str, Any]]
    if body.format == "jsonl":
        raw_rows = [json.loads(line) for line in body.content.splitlines() if line.strip()]
    else:
        raw_rows = [dict(row) for row in csv.DictReader(io.StringIO(body.content))]
        for row in raw_rows:
            metadata_raw = row.pop("metadata", "")
            tags_raw = row.pop("tags", "")
            metadata = json.loads(metadata_raw) if metadata_raw else {}
            for key in (
                "customer_tier", "days_since_purchase", "security_flag",
                "amount_minor", "currency", "amount_source",
            ):
                value = row.get(key, "")
                if value == "":
                    row.pop(key, None)
                    continue
                if key == "days_since_purchase":
                    value = int(value)
                elif key == "amount_minor":
                    value = int(value)
                elif key == "security_flag":
                    value = str(value).strip().lower() in {"1", "true", "yes"}
                row[key] = value
            row["metadata"] = metadata
            if tags_raw:
                row["tags"] = (
                    json.loads(tags_raw)
                    if tags_raw.lstrip().startswith("[")
                    else [item.strip() for item in tags_raw.split(";") if item.strip()]
                )
    if not raw_rows or len(raw_rows) > 500:
        raise ValueError("imports must contain between 1 and 500 tickets")
    return [TicketCreate.model_validate(row) for row in raw_rows]


@router.post("/tickets/import", status_code=status.HTTP_202_ACCEPTED)
async def import_tickets(body: TicketImport, request: Request) -> Any:
    try:
        items = _parse_ticket_document(body)
    except (ValueError, TypeError, json.JSONDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=422, detail=f"invalid ticket import: {exc}") from exc
    imported = []
    for item in items:
        ticket = await _runtime(request).repository.create_ticket(
            external_id=item.external_id,
            subject=item.subject,
            body=item.body,
            channel=item.channel,
            customer_ref=item.customer_ref,
            facts={
                "customer_tier": item.customer_tier,
                "days_since_purchase": item.days_since_purchase,
                "security_flag": item.security_flag,
                "amount_minor": item.amount_minor,
                "currency": item.currency,
                "amount_source": item.amount_source,
            },
            metadata=item.metadata,
            tags=item.tags,
        )
        queued = await _runtime(request).runs.queue_ticket_run(
            ticket_id=str(ticket["id"]),
            mode="live",
            idempotency_key=f"import:{ticket['id']}:v{ticket['version']}",
        )
        imported.append(
            {"ticket_id": ticket["id"], "run_id": queued["run"]["id"], "job_id": queued["job"]["id"]}
        )
        _runtime(request).runs.emit_event(
            "ticket.ingested",
            {"ticket_id": str(ticket["id"]), "run_id": str(queued["run"]["id"])},
        )
    return jsonable_encoder({"accepted": len(imported), "items": imported})


@router.get("/tickets")
async def list_tickets(request: Request, limit: int = 100) -> Any:
    return jsonable_encoder(await _runtime(request).repository.list_tickets(limit=limit))


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request) -> Any:
    ticket = await _runtime(request).repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return jsonable_encoder(ticket)


@router.post("/tickets/{ticket_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def queue_run(
    ticket_id: str,
    body: RunCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> Any:
    try:
        queued = await _runtime(request).runs.queue_ticket_run(
            ticket_id=ticket_id,
            mode=body.mode,
            kind=body.kind,
            idempotency_key=idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response.headers["Location"] = f"/api/v1/runs/{queued['run']['id']}"
    return jsonable_encoder(
        {
            "run_id": queued["run"]["id"],
            "job_id": queued["job"]["id"],
            "status": queued["run"]["status"],
            "manifest_id": queued["run"]["manifest_id"],
        }
    )


@router.get("/runs")
async def list_runs(request: Request, limit: int = 100) -> Any:
    return jsonable_encoder(await _runtime(request).repository.list_runs(limit=limit))


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> Any:
    run = await _runtime(request).repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return jsonable_encoder(run)


@router.get("/failures")
async def list_failures(request: Request, limit: int = 100) -> Any:
    return jsonable_encoder(await _runtime(request).repository.list_failures(limit=limit))


@router.get("/failures/{failure_id}")
async def get_failure(failure_id: str, request: Request) -> Any:
    failure = await _runtime(request).repository.get_failure(failure_id)
    if failure is None:
        raise HTTPException(status_code=404, detail="failure not found")
    return jsonable_encoder(failure)


@router.post("/failures/{failure_id}/corrections", status_code=status.HTTP_201_CREATED)
async def propose_failure_correction(failure_id: str, request: Request) -> Any:
    try:
        prepared = await _runtime(request).corrections.propose_for_failure(failure_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CorrectionGenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _runtime(request).runs.emit_event(
        "correction.proposed",
        {"correction_id": str(prepared["id"]), "failure_id": failure_id},
    )
    return jsonable_encoder(prepared)


@router.get("/triage")
async def list_triage(request: Request, limit: int = 100) -> Any:
    return jsonable_encoder(await _runtime(request).repository.list_triage_items(limit=limit))


@router.get("/judge/calibration")
async def judge_calibration(request: Request) -> Any:
    return jsonable_encoder(await _runtime(request).repository.judge_calibration())


@router.post("/triage/{item_id}/resolve")
async def resolve_triage(item_id: str, body: TriageResolution, request: Request) -> Any:
    try:
        item = await _runtime(request).repository.resolve_triage_item(
            item_id,
            decision=body.decision,
            actor=body.actor,
            expected_action=body.expected_action,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return jsonable_encoder(item)


@router.get("/corrections")
async def list_corrections(request: Request, limit: int = 100) -> Any:
    return jsonable_encoder(
        await asyncio.to_thread(_runtime(request).stores.ledger.list_corrections, limit=limit)
    )


@router.post("/corrections/{correction_id}/approve")
async def approve_correction(
    correction_id: str,
    request: Request,
    body: CorrectionReview | None = None,
) -> Any:
    review = body or CorrectionReview()
    try:
        result = await _runtime(request).approvals.approve(
            correction_id,
            actor=review.actor,
            channel=review.channel,
            note=review.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return jsonable_encoder(result)


@router.post("/corrections/{correction_id}/reject")
async def reject_correction(
    correction_id: str,
    request: Request,
    body: CorrectionReview | None = None,
) -> Any:
    review = body or CorrectionReview()
    try:
        result = await _runtime(request).approvals.reject(
            correction_id,
            actor=review.actor,
            channel=review.channel,
            note=review.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return jsonable_encoder(result)


@router.get("/policies")
async def list_policies(request: Request) -> Any:
    runtime = _runtime(request)
    return jsonable_encoder(await asyncio.to_thread(runtime.stores.redis.get_policy_rules))


@router.post("/policies/compile", status_code=status.HTTP_201_CREATED)
async def compile_policy_rule(body: PolicyCompileRequest, request: Request) -> Any:
    try:
        proposal = await _runtime(request).corrections.compile_policy(
            source_text=body.source_text,
            source_doc_ref=body.source_doc_ref,
        )
    except PolicyCompilationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return jsonable_encoder(proposal)


@router.get("/artifacts")
async def list_artifacts(request: Request) -> Any:
    runtime = _runtime(request)
    keys = ["routing:rules", "memory:policy:refund_window", "config:max_transitions"]
    histories = await asyncio.gather(
        *(
            asyncio.to_thread(runtime.stores.ledger.artifact_history, key)
            for key in keys
        )
    )
    return jsonable_encoder(
        [
            {"artifact_key": key, "versions": history, "latest": history[-1] if history else None}
            for key, history in zip(keys, histories, strict=True)
        ]
    )


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    runtime = _runtime(request)
    initial_id = request.headers.get("Last-Event-ID", "$")

    async def stream():
        cursor = initial_id
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(
                runtime.stores.redis.xread,
                "product",
                last_id=cursor,
                block_ms=15_000,
                count=100,
            )
            if not rows:
                yield ": keepalive\n\n"
                continue
            for row in rows:
                cursor = str(row["id"])
                event = str(row.get("event") or "loopie")
                payload = json.dumps(jsonable_encoder(row.get("data") or {}), separators=(",", ":"))
                yield f"id: {cursor}\nevent: {event}\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
