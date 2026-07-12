"""Durable lease queue boundary for long-running Loopie work.

The queue decides *whether* a run should execute and who currently owns it.
LangGraph checkpoints decide *where* graph execution resumes. Lease tokens are
fencing tokens: a stale worker cannot complete a job after its lease is reclaimed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Literal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.loopie.manifests import DEFAULT_PROJECT_ID

JobStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(frozen=True)
class Job:
    id: str
    project_id: str
    job_type: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    idempotency_key: str
    created_at: datetime
    next_attempt_at: datetime
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    error: str | None = None


CLAIM_SQL = """
WITH candidate AS (
    SELECT id
    FROM loopie.jobs
    WHERE (
        (status = 'queued' AND next_attempt_at <= NOW())
        OR (status = 'running' AND lease_expires_at < NOW())
    )
      AND attempts < max_attempts
    ORDER BY created_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE loopie.jobs AS job
SET status = 'running',
    lease_owner = %(worker_id)s,
    lease_token = %(lease_token)s,
    lease_expires_at = NOW() + (%(lease_seconds)s * INTERVAL '1 second'),
    heartbeat_at = NOW(),
    attempts = attempts + 1,
    error = NULL
FROM candidate
WHERE job.id = candidate.id
RETURNING job.*
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _row_to_job(row: dict[str, Any] | None) -> Job | None:
    if row is None:
        return None
    return Job(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        job_type=str(row["type"]),
        payload=dict(row.get("payload") or {}),
        status=row["status"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        idempotency_key=str(row["idempotency_key"]),
        created_at=row["created_at"],
        next_attempt_at=row["next_attempt_at"],
        lease_owner=row.get("lease_owner"),
        lease_token=str(row["lease_token"]) if row.get("lease_token") else None,
        lease_expires_at=row.get("lease_expires_at"),
        heartbeat_at=row.get("heartbeat_at"),
        error=row.get("error"),
    )


class PostgresJobStore:
    """Async repository using the app's shared Postgres connection pool."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> Job:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                row = await cursor.execute(
                    """
                    INSERT INTO loopie.jobs
                        (id, project_id, type, payload, status, attempts, max_attempts,
                         idempotency_key, next_attempt_at)
                    VALUES
                        (%(id)s, %(project_id)s, %(type)s, %(payload)s::jsonb, 'queued', 0,
                         %(max_attempts)s, %(idempotency_key)s, NOW())
                    ON CONFLICT (project_id, idempotency_key) DO UPDATE
                    SET idempotency_key = EXCLUDED.idempotency_key
                    RETURNING *
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "project_id": project_id,
                        "type": job_type,
                        "payload": json.dumps(payload),
                        "max_attempts": max_attempts,
                        "idempotency_key": idempotency_key,
                    },
                )
                return _row_to_job(await row.fetchone())  # type: ignore[return-value]

    async def claim(self, *, worker_id: str, lease_seconds: int = 30) -> Job | None:
        lease_token = str(uuid.uuid4())
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cursor:
                    result = await cursor.execute(
                        CLAIM_SQL,
                        {
                            "worker_id": worker_id,
                            "lease_token": lease_token,
                            "lease_seconds": lease_seconds,
                        },
                    )
                    return _row_to_job(await result.fetchone())

    async def heartbeat(self, *, job_id: str, lease_token: str, lease_seconds: int = 30) -> bool:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                UPDATE loopie.jobs
                SET heartbeat_at = NOW(),
                    lease_expires_at = NOW() + (%(lease_seconds)s * INTERVAL '1 second')
                WHERE id = %(job_id)s AND status = 'running'
                  AND lease_token = %(lease_token)s
                """,
                {
                    "job_id": job_id,
                    "lease_token": lease_token,
                    "lease_seconds": lease_seconds,
                },
            )
            return result.rowcount == 1

    async def complete(self, *, job_id: str, lease_token: str) -> bool:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                UPDATE loopie.jobs
                SET status = 'succeeded', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NOW()
                WHERE id = %(job_id)s AND status = 'running'
                  AND lease_token = %(lease_token)s
                """,
                {"job_id": job_id, "lease_token": lease_token},
            )
            return result.rowcount == 1

    async def fail(self, *, job_id: str, lease_token: str, error: str) -> Job | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    UPDATE loopie.jobs
                    SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                        next_attempt_at = NOW() + (
                            LEAST(POWER(2, GREATEST(attempts - 1, 0)), 60) * INTERVAL '1 second'
                        ),
                        error = LEFT(%(error)s, 2000),
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, heartbeat_at = NOW()
                    WHERE id = %(job_id)s AND status = 'running'
                      AND lease_token = %(lease_token)s
                    RETURNING *
                    """,
                    {"job_id": job_id, "lease_token": lease_token, "error": error},
                )
                return _row_to_job(await result.fetchone())


class MemoryJobStore:
    """Deterministic test implementation with the same lease/fencing semantics."""

    def __init__(self, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.clock = clock
        self.jobs: dict[str, Job] = {}
        self.by_idempotency_key: dict[tuple[str, str], str] = {}

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> Job:
        scope_key = (project_id, idempotency_key)
        existing_id = self.by_idempotency_key.get(scope_key)
        if existing_id:
            return self.jobs[existing_id]
        now = self.clock()
        job = Job(
            id=str(uuid.uuid4()),
            project_id=project_id,
            job_type=job_type,
            payload=payload,
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            created_at=now,
            next_attempt_at=now,
        )
        self.jobs[job.id] = job
        self.by_idempotency_key[scope_key] = job.id
        return job

    async def claim(self, *, worker_id: str, lease_seconds: int = 30) -> Job | None:
        now = self.clock()
        candidates = [
            job
            for job in self.jobs.values()
            if job.attempts < job.max_attempts
            and (
                (job.status == "queued" and job.next_attempt_at <= now)
                or (
                    job.status == "running"
                    and job.lease_expires_at is not None
                    and job.lease_expires_at < now
                )
            )
        ]
        if not candidates:
            return None
        job = min(candidates, key=lambda item: (item.created_at, item.id))
        claimed = replace(
            job,
            status="running",
            attempts=job.attempts + 1,
            lease_owner=worker_id,
            lease_token=str(uuid.uuid4()),
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            error=None,
        )
        self.jobs[job.id] = claimed
        return claimed

    async def heartbeat(self, *, job_id: str, lease_token: str, lease_seconds: int = 30) -> bool:
        job = self.jobs[job_id]
        if job.status != "running" or job.lease_token != lease_token:
            return False
        now = self.clock()
        self.jobs[job_id] = replace(
            job,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        return True

    async def complete(self, *, job_id: str, lease_token: str) -> bool:
        job = self.jobs[job_id]
        if job.status != "running" or job.lease_token != lease_token:
            return False
        self.jobs[job_id] = replace(
            job,
            status="succeeded",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=self.clock(),
        )
        return True

    async def fail(self, *, job_id: str, lease_token: str, error: str) -> Job | None:
        job = self.jobs[job_id]
        if job.status != "running" or job.lease_token != lease_token:
            return None
        now = self.clock()
        terminal = job.attempts >= job.max_attempts
        failed = replace(
            job,
            status="failed" if terminal else "queued",
            next_attempt_at=now + timedelta(seconds=min(2 ** max(job.attempts - 1, 0), 60)),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
            error=error[:2000],
        )
        self.jobs[job_id] = failed
        return failed
