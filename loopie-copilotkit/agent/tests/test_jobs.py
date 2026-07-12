"""Lease, recovery, retry, idempotency, and fencing semantics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from src.loopie.jobs import CLAIM_SQL, MemoryJobStore


class Clock:
    def __init__(self):
        self.now = datetime(2026, 7, 11, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds: int):
        self.now += timedelta(seconds=seconds)


def run(coro):
    return asyncio.run(coro)


def test_enqueue_is_idempotent_and_claims_once():
    store = MemoryJobStore()
    first = run(store.enqueue(job_type="run_ticket", payload={"ticket_id": "t1"}, idempotency_key="k1"))
    second = run(store.enqueue(job_type="run_ticket", payload={"ticket_id": "t2"}, idempotency_key="k1"))

    assert first.id == second.id
    claimed = run(store.claim(worker_id="worker-a", lease_seconds=30))
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.attempts == 1
    assert run(store.claim(worker_id="worker-b", lease_seconds=30)) is None


def test_expired_lease_is_reclaimed_and_old_token_is_fenced():
    clock = Clock()
    store = MemoryJobStore(clock=clock)
    job = run(store.enqueue(job_type="run_ticket", payload={}, idempotency_key="k1"))
    first = run(store.claim(worker_id="worker-a", lease_seconds=10))
    assert first is not None

    clock.advance(11)
    second = run(store.claim(worker_id="worker-b", lease_seconds=10))
    assert second is not None
    assert second.id == job.id
    assert second.attempts == 2
    assert second.lease_token != first.lease_token
    assert run(store.complete(job_id=job.id, lease_token=first.lease_token or "")) is False
    assert run(store.complete(job_id=job.id, lease_token=second.lease_token or "")) is True


def test_fail_retries_with_backoff_then_becomes_terminal():
    clock = Clock()
    store = MemoryJobStore(clock=clock)
    job = run(
        store.enqueue(job_type="run_ticket", payload={}, idempotency_key="k1", max_attempts=2)
    )

    first = run(store.claim(worker_id="worker", lease_seconds=10))
    retried = run(store.fail(job_id=job.id, lease_token=first.lease_token or "", error="transient"))
    assert retried is not None
    assert retried.status == "queued"
    assert run(store.claim(worker_id="worker")) is None

    clock.advance(1)
    second = run(store.claim(worker_id="worker", lease_seconds=10))
    terminal = run(store.fail(job_id=job.id, lease_token=second.lease_token or "", error="permanent"))
    assert terminal is not None
    assert terminal.status == "failed"
    assert run(store.claim(worker_id="worker")) is None


def test_heartbeat_requires_current_fencing_token():
    store = MemoryJobStore()
    job = run(store.enqueue(job_type="run_ticket", payload={}, idempotency_key="k1"))
    claimed = run(store.claim(worker_id="worker", lease_seconds=10))
    assert claimed is not None
    assert run(store.heartbeat(job_id=job.id, lease_token="stale")) is False
    assert run(store.heartbeat(job_id=job.id, lease_token=claimed.lease_token or "")) is True


def test_postgres_claim_contract_uses_skip_locked_and_fencing():
    normalized = " ".join(CLAIM_SQL.split()).upper()
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert "LEASE_TOKEN" in normalized
    assert "ATTEMPTS < MAX_ATTEMPTS" in normalized
