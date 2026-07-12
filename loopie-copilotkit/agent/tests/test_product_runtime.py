from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.loopie.api.v1 import router
from src.loopie.jobs import MemoryJobStore
from src.loopie.product_repository import MemoryProductRepository
from src.loopie.services.runs import RunService
from src.loopie.worker import DurableWorker

from memory_stores import MemoryLedger, MemoryRedis


def _run(coro):
    return asyncio.run(coro)


def _seed(redis: MemoryRedis) -> None:
    redis.set_memory("policy:refund_window", "Refunds allowed within 30 days.", version=1)
    redis.set_routing_rules([])
    redis.set_policy_rules([])
    redis.set_config("max_transitions", 6)


async def _runtime_parts():
    redis = MemoryRedis()
    _seed(redis)
    repository = MemoryProductRepository()
    jobs = MemoryJobStore()
    runs = RunService(repository=repository, jobs=jobs, redis=redis, ledger=MemoryLedger())
    ticket = await repository.create_ticket(
        external_id="support-1",
        subject="Refund request",
        body="Please refund the purchase from five days ago.",
        channel="api",
        customer_ref="customer-1",
        metadata={
            "days_since_purchase": 5,
            "customer_tier": "standard",
            "security_flag": False,
        },
        tags=["refund"],
    )
    return redis, repository, jobs, runs, ticket


def test_worker_executes_the_manifest_pinned_when_run_was_queued() -> None:
    async def scenario():
        redis, repository, jobs, runs, ticket = await _runtime_parts()
        queued = await runs.queue_ticket_run(
            ticket_id=ticket["id"],
            mode="test",
            idempotency_key="client-request-1",
        )
        # A correction lands after the API accepted the run. The worker must
        # still execute the exact accepted manifest, not this newer projection.
        redis.set_memory("policy:refund_window", "Changed after queue.", version=99)
        redis.set_routing_rules([{"rule": "security_flag_blocks_refund"}])

        worker = DurableWorker(jobs=jobs, runs=runs, worker_id="test-worker")
        assert await worker.run_once() is True
        stored = await repository.get_run(queued["run"]["id"])
        assert stored and stored["status"] == "succeeded"
        decision = stored["decision"]
        assert decision["memory_version"] == 1
        assert decision["artifacts_snapshot"]["routing_rules"] == []
        assert {item["key"] for item in decision["read_set"]} == {
            "memory:policy:refund_window",
            "routing:rules",
            "policy:rules",
            "config:max_transitions",
            "config:action_taxonomy",
        }

    _run(scenario())


def test_run_queue_is_idempotent_for_client_retry() -> None:
    async def scenario():
        _, _, _, runs, ticket = await _runtime_parts()
        first, second = await asyncio.gather(
            runs.queue_ticket_run(ticket_id=ticket["id"], mode="test", idempotency_key="same"),
            runs.queue_ticket_run(ticket_id=ticket["id"], mode="test", idempotency_key="same"),
        )
        assert first["run"]["id"] == second["run"]["id"]
        assert first["job"]["id"] == second["job"]["id"]

    _run(scenario())


def test_golden_run_joins_annotation_only_after_execution_and_records_golden_failure() -> None:
    async def scenario():
        _, repository, jobs, runs, ticket = await _runtime_parts()
        repository.golden_annotations[ticket["id"]] = {
            "project_id": ticket["project_id"],
            "ticket_id": ticket["id"],
            "expected_action": "deny_refund_offer_credit",
            "failure_seed": None,
            "declared_neighbors": [],
            "expected_metadata": {},
            "source": "fixture",
            "annotated_by": "test",
        }
        queued = await runs.queue_ticket_run(
            ticket_id=ticket["id"],
            mode="test",
            kind="golden",
            idempotency_key="golden-run",
        )
        worker = DurableWorker(jobs=jobs, runs=runs, worker_id="golden-worker")
        assert await worker.run_once() is True
        stored = await repository.get_run(queued["run"]["id"])
        assert stored["decision"]["correctness"]["golden"]["passed"] is False
        failure = next(iter(repository.failures.values()))
        assert failure["layer"] == "golden"
        assert failure["category"] == "golden_mismatch"

    _run(scenario())


def test_ticket_api_rejects_golden_label_leak_and_returns_202_run_handle() -> None:
    async def build():
        _, repository, jobs, runs, _ = await _runtime_parts()
        return repository, jobs, runs

    repository, jobs, runs = _run(build())
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(repository=repository, jobs=jobs, runs=runs)
    with TestClient(app) as client:
        leaked = client.post(
            "/api/v1/tickets",
            json={
                "external_id": "bad",
                "subject": "bad",
                "body": "bad",
                "metadata": {"expected_action": "approve_refund"},
            },
        )
        assert leaked.status_code == 422

        ticket = client.post(
            "/api/v1/tickets",
            json={
                "external_id": "api-ticket",
                "subject": "Refund",
                "body": "Refund from day 3",
                "metadata": {
                    "days_since_purchase": 3,
                    "customer_tier": "standard",
                    "security_flag": False,
                },
            },
        )
        assert ticket.status_code == 201
        ticket_id = ticket.json()["id"]
        queued = client.post(
            f"/api/v1/tickets/{ticket_id}/runs",
            headers={"Idempotency-Key": "api-run-1"},
            json={"mode": "test", "kind": "ticket"},
        )
        assert queued.status_code == 202
        assert queued.headers["location"].endswith(queued.json()["run_id"])
        assert queued.json()["status"] == "queued"


def test_jsonl_and_csv_document_imports_queue_every_ticket() -> None:
    async def build():
        _, repository, jobs, runs, _ = await _runtime_parts()
        return repository, jobs, runs

    repository, jobs, runs = _run(build())
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = SimpleNamespace(repository=repository, jobs=jobs, runs=runs)
    with TestClient(app) as client:
        jsonl = client.post(
            "/api/v1/tickets/import",
            json={
                "format": "jsonl",
                "content": (
                    '{"external_id":"jsonl-1","subject":"Refund","body":"Refund please",'
                    '"metadata":{"days_since_purchase":2,"security_flag":false}}\n'
                ),
            },
        )
        assert jsonl.status_code == 202
        assert jsonl.json()["accepted"] == 1

        csv_result = client.post(
            "/api/v1/tickets/import",
            json={
                "format": "csv",
                "content": (
                    "external_id,subject,body,days_since_purchase,security_flag,tags\n"
                    "csv-1,Billing help,Please review,4,false,billing;review\n"
                ),
            },
        )
        assert csv_result.status_code == 202
        assert csv_result.json()["accepted"] == 1
