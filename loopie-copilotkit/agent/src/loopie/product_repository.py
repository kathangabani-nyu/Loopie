"""Durable product records for tickets, runs, manifests, and read sets."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.loopie.config import get_settings
from src.loopie.manifests import (
    DEFAULT_PROJECT_ID,
    ArtifactSnapshot,
    RunManifest,
)

FORBIDDEN_LIVE_TICKET_KEYS = frozenset(
    {
        "expected_action",
        "expected_tool_calls",
        "failure_seed",
        "expected_failure_baseline",
        "expected_memory_version",
        "diagnosis_hint",
        "neighbors",
    }
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _score_vector(correctness: dict[str, Any] | None) -> dict[str, bool]:
    correctness = correctness or {}
    scores = {"policy": bool(correctness.get("policy", {}).get("passed", True))}
    scores.update(correctness.get("structural", {}).get("scores", {}))
    golden = correctness.get("golden") or {}
    scores.update({f"golden:{key}": value for key, value in golden.get("scores", {}).items()})
    return {key: bool(value) for key, value in scores.items()}


def _attach_improvement_proof(
    result: dict[str, Any],
    *,
    parent_decision: dict[str, Any] | None,
    parent_run_id: str | None,
    correction_id: str | None,
) -> dict[str, Any] | None:
    if not parent_run_id or not correction_id or not parent_decision:
        return None
    baseline = parent_decision.get("correctness") or {}
    patched = result.get("correctness") or {}
    baseline_scores = _score_vector(baseline)
    patched_scores = _score_vector(patched)
    improved = sorted(
        key for key, passed in patched_scores.items() if passed and not baseline_scores.get(key, True)
    )
    regressed = sorted(
        key for key, passed in baseline_scores.items() if passed and not patched_scores.get(key, True)
    )
    proof = {
        "parent_run_id": parent_run_id,
        "correction_id": correction_id,
        "baseline_passed": bool(baseline.get("passed", True)),
        "patched_passed": bool(patched.get("passed", True)),
        "improved_scores": improved,
        "regressed_scores": regressed,
        "improvement_proven": (
            not bool(baseline.get("passed", True))
            and bool(patched.get("passed", True))
            and bool(improved)
            and not regressed
        ),
    }
    result["improvement_proof"] = proof
    return proof


def ticket_to_agent_input(ticket: dict[str, Any]) -> dict[str, Any]:
    """Build the live decision input without ever joining golden annotations."""
    metadata = {
        key: value
        for key, value in dict(ticket.get("metadata") or {}).items()
        if key not in FORBIDDEN_LIVE_TICKET_KEYS
    }
    return {
        "case_id": ticket["external_id"],
        "version": int(ticket.get("version", 1)),
        "request": ticket["body"],
        **metadata,
    }


class ProductRepository(Protocol):
    async def get_project(self, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None: ...
    async def create_ticket(self, *, external_id: str, subject: str, body: str, channel: str, customer_ref: str | None, metadata: dict[str, Any], tags: list[str], project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]: ...
    async def list_tickets(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]: ...
    async def get_ticket(self, ticket_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None: ...
    async def get_golden_annotation(self, ticket_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None: ...
    async def queue_run(self, *, ticket: dict[str, Any], manifest: RunManifest, mode: str, kind: str, idempotency_key: str, parent_run_id: str | None = None, correction_id: str | None = None, project_id: str = DEFAULT_PROJECT_ID) -> tuple[dict[str, Any], dict[str, Any]]: ...
    async def get_run(self, run_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None: ...
    async def list_runs(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]: ...
    async def list_failures(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]: ...
    async def get_failure(self, failure_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None: ...
    async def tickets_affected_by_artifact(self, artifact_key: str, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]: ...
    async def list_triage_items(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]: ...
    async def create_triage_item(self, *, run_id: str, verdict: dict[str, Any], confidence: float, calibration_sample: bool, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]: ...
    async def judge_calibration(self, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]: ...
    async def resolve_triage_item(self, item_id: str, *, decision: str, actor: str, expected_action: str | None = None, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]: ...
    async def get_run_manifest(self, manifest_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> RunManifest | None: ...
    async def mark_run_running(self, run_id: str) -> None: ...
    async def mark_run_queued(self, run_id: str, error: str) -> None: ...
    async def finish_run(self, run_id: str, result: dict[str, Any]) -> None: ...
    async def fail_run(self, run_id: str, error: str) -> None: ...


def _manifest_columns(manifest: RunManifest) -> dict[str, Any]:
    record = manifest.to_record()
    return {
        "id": manifest.id,
        "ticket_version": manifest.ticket_version,
        "artifact_contents": {
            item["key"]: item["value"] for item in record["artifacts"]
        },
        "artifact_hashes": {
            item["key"]: {
                "version": item["version"],
                "content_hash": item["content_hash"],
            }
            for item in record["artifacts"]
        },
        "prompt_versions": {"decision": manifest.prompt_version},
        "schema_versions": {"decision": manifest.schema_version},
        "model_config": {"model": manifest.model_version},
        "tool_versions": {"swarm": manifest.tool_version},
        "code_version": manifest.code_version,
        "content_hash": manifest.content_hash,
        "created_at": manifest.created_at,
    }


def _manifest_from_row(row: dict[str, Any]) -> RunManifest:
    contents = dict(row["artifact_contents"])
    hashes = dict(row["artifact_hashes"])
    artifacts = tuple(
        ArtifactSnapshot.capture(
            key,
            value,
            version=hashes[key]["version"],
        )
        for key, value in sorted(contents.items())
    )
    for item in artifacts:
        if item.content_hash != hashes[item.key]["content_hash"]:
            raise RuntimeError(f"Manifest artifact hash mismatch for {item.key}")
    return RunManifest(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        ticket_id=str(row["external_id"]),
        ticket_version=int(row["ticket_version"]),
        artifacts=artifacts,
        prompt_version=str(row["prompt_versions"]["decision"]),
        schema_version=str(row["schema_versions"]["decision"]),
        model_version=str(row["model_config"]["model"]),
        tool_version=str(row["tool_versions"]["swarm"]),
        code_version=str(row["code_version"]),
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
    )


class PostgresProductRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def get_project(self, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute("SELECT * FROM loopie.projects WHERE id = %s", (project_id,))
                row = await result.fetchone()
                return dict(row) if row else None

    async def create_ticket(
        self,
        *,
        external_id: str,
        subject: str,
        body: str,
        channel: str = "api",
        customer_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    INSERT INTO loopie.tickets
                        (id, project_id, external_id, subject, body, channel, customer_ref, metadata, tags)
                    VALUES (%(id)s, %(project_id)s, %(external_id)s, %(subject)s, %(body)s,
                            %(channel)s, %(customer_ref)s, %(metadata)s::jsonb, %(tags)s)
                    ON CONFLICT (project_id, external_id) DO UPDATE
                    SET subject = EXCLUDED.subject, body = EXCLUDED.body,
                        channel = EXCLUDED.channel, customer_ref = EXCLUDED.customer_ref,
                        metadata = EXCLUDED.metadata,
                        tags = EXCLUDED.tags, version = loopie.tickets.version + 1,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "project_id": project_id,
                        "external_id": external_id,
                        "subject": subject,
                        "body": body,
                        "channel": channel,
                        "customer_ref": customer_ref,
                        "metadata": json.dumps(metadata or {}),
                        "tags": tags or [],
                    },
                )
                return dict(await result.fetchone())

    async def list_tickets(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    "SELECT * FROM loopie.tickets WHERE project_id = %s ORDER BY created_at DESC LIMIT %s",
                    (project_id, min(max(limit, 1), 500)),
                )
                return [dict(row) for row in await result.fetchall()]

    async def get_ticket(self, ticket_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    "SELECT * FROM loopie.tickets WHERE id = %s AND project_id = %s",
                    (ticket_id, project_id),
                )
                row = await result.fetchone()
                return dict(row) if row else None

    async def get_golden_annotation(
        self,
        ticket_id: str,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT * FROM loopie.golden_annotations
                    WHERE ticket_id = %s AND project_id = %s
                    """,
                    (ticket_id, project_id),
                )
                row = await result.fetchone()
                return dict(row) if row else None

    async def queue_run(
        self,
        *,
        ticket: dict[str, Any],
        manifest: RunManifest,
        mode: str,
        kind: str = "ticket",
        idempotency_key: str,
        parent_run_id: str | None = None,
        correction_id: str | None = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        columns = _manifest_columns(manifest)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # Serialize this idempotency scope before inserting the immutable
                # manifest. Without the advisory lock, a concurrent duplicate
                # could leave an unreachable manifest row behind.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{project_id}:{idempotency_key}",),
                )
                async with conn.cursor(row_factory=dict_row) as cursor:
                    existing_result = await cursor.execute(
                        """
                        SELECT run.*, job.id AS job_id, job.type AS job_type,
                               job.payload AS job_payload, job.status AS job_status,
                               job.idempotency_key AS job_idempotency_key,
                               job.created_at AS job_created_at
                        FROM loopie.runs AS run
                        JOIN loopie.jobs AS job
                          ON job.project_id = run.project_id
                         AND job.idempotency_key = 'run:' || run.id::text
                        WHERE run.project_id = %s AND run.idempotency_key = %s
                        """,
                        (project_id, idempotency_key),
                    )
                    existing = await existing_result.fetchone()
                    if existing:
                        existing = dict(existing)
                        job = {
                            "id": existing.pop("job_id"),
                            "project_id": existing["project_id"],
                            "type": existing.pop("job_type"),
                            "payload": existing.pop("job_payload"),
                            "status": existing.pop("job_status"),
                            "idempotency_key": existing.pop("job_idempotency_key"),
                            "created_at": existing.pop("job_created_at"),
                        }
                        return existing, job

                run_id = str(uuid.uuid4())
                job_id = str(uuid.uuid5(uuid.UUID(run_id), "execute"))
                await conn.execute(
                    """
                    INSERT INTO loopie.run_manifests
                        (id, project_id, ticket_id, ticket_version, artifact_contents,
                         artifact_hashes, prompt_versions, schema_versions, model_config,
                         tool_versions, code_version, content_hash, created_at)
                    VALUES
                        (%(id)s, %(project_id)s, %(ticket_id)s, %(ticket_version)s,
                         %(artifact_contents)s::jsonb, %(artifact_hashes)s::jsonb,
                         %(prompt_versions)s::jsonb, %(schema_versions)s::jsonb,
                         %(model_config)s::jsonb, %(tool_versions)s::jsonb,
                         %(code_version)s, %(content_hash)s, %(created_at)s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        **columns,
                        "project_id": project_id,
                        "ticket_id": str(ticket["id"]),
                        **{
                            key: json.dumps(columns[key])
                            for key in (
                                "artifact_contents",
                                "artifact_hashes",
                                "prompt_versions",
                                "schema_versions",
                                "model_config",
                                "tool_versions",
                            )
                        },
                    },
                )
                async with conn.cursor(row_factory=dict_row) as cursor:
                    result = await cursor.execute(
                        """
                        INSERT INTO loopie.runs
                            (id, project_id, idempotency_key, kind, mode, status,
                             ticket_id, parent_run_id, correction_id, manifest_id)
                        VALUES (%(id)s, %(project_id)s, %(idempotency_key)s, %(kind)s,
                                %(mode)s, 'queued', %(ticket_id)s, %(parent_run_id)s,
                                %(correction_id)s, %(manifest_id)s)
                        RETURNING *
                        """,
                        {
                            "id": run_id,
                            "project_id": project_id,
                            "idempotency_key": idempotency_key,
                            "kind": kind,
                            "mode": mode,
                            "ticket_id": str(ticket["id"]),
                            "parent_run_id": parent_run_id,
                            "correction_id": correction_id,
                            "manifest_id": manifest.id,
                        },
                    )
                    run = dict(await result.fetchone())
                    result = await cursor.execute(
                        """
                        INSERT INTO loopie.jobs
                            (id, project_id, type, payload, status, attempts, max_attempts,
                             idempotency_key, next_attempt_at)
                        VALUES (%(id)s, %(project_id)s, 'execute_run', %(payload)s::jsonb,
                                'queued', 0, 3, %(idempotency_key)s, NOW())
                        RETURNING *
                        """,
                        {
                            "id": job_id,
                            "project_id": project_id,
                            "payload": json.dumps({"run_id": str(run["id"])}),
                            "idempotency_key": f"run:{run['id']}",
                        },
                    )
                    job = dict(await result.fetchone())
                return run, job

    async def get_run(self, run_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    "SELECT * FROM loopie.runs WHERE id = %s AND project_id = %s",
                    (run_id, project_id),
                )
                row = await result.fetchone()
                return dict(row) if row else None

    async def list_runs(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    "SELECT * FROM loopie.runs WHERE project_id = %s ORDER BY created_at DESC LIMIT %s",
                    (project_id, min(max(limit, 1), 500)),
                )
                return [dict(row) for row in await result.fetchall()]

    async def list_failures(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT failure.*, result.run_id AS eval_run_id, result.ticket_id,
                           result.decision, result.scores, ticket.external_id
                    FROM loopie.failures AS failure
                    JOIN loopie.eval_case_results AS result ON result.id = failure.eval_case_result_id
                    LEFT JOIN loopie.tickets AS ticket ON ticket.id = result.ticket_id
                    WHERE failure.project_id = %s
                    ORDER BY failure.created_at DESC LIMIT %s
                    """,
                    (project_id, min(max(limit, 1), 500)),
                )
                return [dict(row) for row in await result.fetchall()]

    async def get_failure(self, failure_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT failure.*, eval_result.decision, eval_result.scores,
                           run.id AS run_id, run.mode, run.kind,
                           ticket.id AS ticket_id, ticket.external_id, ticket.version,
                           ticket.subject, ticket.body, ticket.channel, ticket.customer_ref,
                           ticket.metadata, ticket.tags
                    FROM loopie.failures AS failure
                    JOIN loopie.eval_case_results AS eval_result ON eval_result.id = failure.eval_case_result_id
                    JOIN loopie.eval_runs AS eval_run ON eval_run.id = eval_result.eval_run_id
                    JOIN loopie.runs AS run ON run.id = eval_run.run_id
                    JOIN loopie.tickets AS ticket ON ticket.id = run.ticket_id
                    WHERE failure.id = %s AND failure.project_id = %s
                    """,
                    (failure_id, project_id),
                )
                row = await result.fetchone()
                return dict(row) if row else None

    async def tickets_affected_by_artifact(self, artifact_key: str, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT DISTINCT ON (ticket.id) ticket.*
                    FROM loopie.run_read_sets AS read_set
                    JOIN loopie.runs AS run ON run.id = read_set.run_id
                    JOIN loopie.tickets AS ticket ON ticket.id = run.ticket_id
                    WHERE read_set.project_id = %s AND read_set.artifact_key = %s
                    ORDER BY ticket.id, run.created_at DESC LIMIT %s
                    """,
                    (project_id, artifact_key, min(max(limit, 1), 500)),
                )
                return [dict(row) for row in await result.fetchall()]

    async def list_triage_items(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT triage.*, run.ticket_id, ticket.external_id
                    FROM loopie.triage_items AS triage
                    JOIN loopie.runs AS run ON run.id = triage.run_id
                    JOIN loopie.tickets AS ticket ON ticket.id = run.ticket_id
                    WHERE triage.project_id = %s
                    ORDER BY triage.created_at DESC LIMIT %s
                    """,
                    (project_id, min(max(limit, 1), 500)),
                )
                return [dict(row) for row in await result.fetchall()]

    async def create_triage_item(self, *, run_id: str, verdict: dict[str, Any], confidence: float, calibration_sample: bool, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    INSERT INTO loopie.triage_items
                        (id, project_id, run_id, judge_verdict, confidence, status, calibration_sample)
                    VALUES (%s, %s, %s, %s::jsonb, %s, 'open', %s)
                    ON CONFLICT (run_id) DO UPDATE
                    SET judge_verdict = EXCLUDED.judge_verdict,
                        confidence = EXCLUDED.confidence,
                        calibration_sample = EXCLUDED.calibration_sample
                    RETURNING *
                    """,
                    (str(uuid.uuid4()), project_id, run_id, json.dumps(verdict), confidence, calibration_sample),
                )
                return dict(await result.fetchone())

    async def judge_calibration(self, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COUNT(*) AS samples,
                           COALESCE(AVG(CASE
                               WHEN triage.judge_verdict->>'suggested_action' = golden.expected_action
                               THEN 1.0 ELSE 0.0 END), 0) AS agreement
                    FROM loopie.triage_items AS triage
                    JOIN loopie.runs AS run ON run.id = triage.run_id
                    JOIN loopie.golden_annotations AS golden ON golden.ticket_id = run.ticket_id
                    WHERE triage.project_id = %s AND triage.calibration_sample = TRUE
                    """,
                    (project_id,),
                )
            ).fetchone()
            samples = int(row["samples"])
            agreement = float(row["agreement"])
            return {
                "samples": samples,
                "agreement": agreement,
                "calibrated": samples >= 10 and agreement >= get_settings().judge_min_calibration,
            }

    async def resolve_triage_item(self, item_id: str, *, decision: str, actor: str, expected_action: str | None = None, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        if decision not in {"confirm", "reject"}:
            raise ValueError("decision must be confirm or reject")
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cursor:
                    result = await cursor.execute(
                        """
                        SELECT triage.*, run.ticket_id
                        FROM loopie.triage_items AS triage
                        JOIN loopie.runs AS run ON run.id = triage.run_id
                        WHERE triage.id = %s AND triage.project_id = %s
                        FOR UPDATE
                        """,
                        (item_id, project_id),
                    )
                    item = await result.fetchone()
                    if item is None:
                        raise KeyError(f"Unknown triage item {item_id}")
                    if item["status"] != "open":
                        return dict(item)
                    if decision == "confirm":
                        if not expected_action:
                            raise ValueError("expected_action is required when confirming")
                        project = await (
                            await conn.execute("SELECT action_taxonomy FROM loopie.projects WHERE id = %s", (project_id,))
                        ).fetchone()
                        if expected_action not in project["action_taxonomy"]:
                            raise ValueError("expected_action is outside the project taxonomy")
                        await conn.execute(
                            """
                            INSERT INTO loopie.golden_annotations
                                (id, project_id, ticket_id, expected_action, source, annotated_by)
                            VALUES (%s, %s, %s, %s, 'human_triage', %s)
                            ON CONFLICT (ticket_id) DO UPDATE
                            SET expected_action = EXCLUDED.expected_action,
                                source = 'human_triage', annotated_by = EXCLUDED.annotated_by
                            """,
                            (str(uuid.uuid4()), project_id, item["ticket_id"], expected_action, actor),
                        )
                    result = await cursor.execute(
                        """
                        UPDATE loopie.triage_items
                        SET status = %s, resolution = %s, resolved_by = %s, resolved_at = NOW()
                        WHERE id = %s RETURNING *
                        """,
                        (
                            "confirmed" if decision == "confirm" else "rejected",
                            "promoted_to_golden" if decision == "confirm" else "dismissed",
                            actor,
                            item_id,
                        ),
                    )
                    return dict(await result.fetchone())

    async def get_run_manifest(self, manifest_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> RunManifest | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                result = await cursor.execute(
                    """
                    SELECT manifest.*, ticket.external_id
                    FROM loopie.run_manifests AS manifest
                    JOIN loopie.tickets AS ticket ON ticket.id = manifest.ticket_id
                    WHERE manifest.id = %s AND manifest.project_id = %s
                    """,
                    (manifest_id, project_id),
                )
                row = await result.fetchone()
                return _manifest_from_row(dict(row)) if row else None

    async def mark_run_running(self, run_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE loopie.runs SET status = 'running', started_at = COALESCE(started_at, NOW()), error = NULL
                WHERE id = %s AND status IN ('queued','running')
                """,
                (run_id,),
            )

    async def mark_run_queued(self, run_id: str, error: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE loopie.runs SET status = 'queued', error = %s WHERE id = %s",
                (error[:2000], run_id),
            )

    async def finish_run(self, run_id: str, result: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                run_row = await (
                    await conn.execute(
                        """
                        SELECT run.project_id, run.ticket_id, run.mode, run.parent_run_id,
                               run.correction_id, parent.decision AS parent_decision,
                               ticket.external_id
                        FROM loopie.runs AS run
                        JOIN loopie.tickets AS ticket ON ticket.id = run.ticket_id
                        LEFT JOIN loopie.runs AS parent ON parent.id = run.parent_run_id
                        WHERE run.id = %s
                        """,
                        (run_id,),
                    )
                ).fetchone()
                if not run_row:
                    raise KeyError(f"Unknown run {run_id}")
                project_id = str(run_row["project_id"] if isinstance(run_row, dict) else run_row[0])
                proof = _attach_improvement_proof(
                    result,
                    parent_decision=run_row.get("parent_decision"),
                    parent_run_id=(
                        str(run_row["parent_run_id"]) if run_row.get("parent_run_id") else None
                    ),
                    correction_id=run_row.get("correction_id"),
                )
                for read in result["read_set"]:
                    await conn.execute(
                        """
                        INSERT INTO loopie.run_read_sets
                            (run_id, project_id, artifact_key, artifact_version, content_hash)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (run_id, artifact_key) DO UPDATE
                        SET artifact_version = EXCLUDED.artifact_version,
                            content_hash = EXCLUDED.content_hash
                        """,
                        (run_id, project_id, read["key"], read["version"], read["content_hash"]),
                    )
                await conn.execute(
                    """
                    UPDATE loopie.runs
                    SET status = 'succeeded', decision = %s::jsonb, finished_at = NOW(), error = NULL
                    WHERE id = %s
                    """,
                    (json.dumps(result), run_id),
                )
                correctness = result.get("correctness") or {"passed": True}
                eval_run_id = f"continuous:{run_id}"
                await conn.execute(
                    """
                    INSERT INTO loopie.eval_runs
                        (id, project_id, run_id, label, suite_name, mode, weave_url, summary)
                    VALUES (%s, %s, %s, 'continuous', 'continuous_ticket', %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE
                    SET weave_url = EXCLUDED.weave_url, summary = EXCLUDED.summary
                    """,
                    (
                        eval_run_id,
                        project_id,
                        run_id,
                        run_row["mode"],
                        (result.get("weave") or {}).get("dashboard_url"),
                        json.dumps({**correctness, "weave": result.get("weave")}),
                    ),
                )
                eval_result = await (
                    await conn.execute(
                        """
                        INSERT INTO loopie.eval_case_results
                            (eval_run_id, project_id, case_id, ticket_id, action, decision,
                             passed, scores, fallback_used, latency_ms)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s)
                        ON CONFLICT (eval_run_id, ticket_id) WHERE ticket_id IS NOT NULL
                        DO UPDATE SET decision = EXCLUDED.decision, passed = EXCLUDED.passed,
                                      scores = EXCLUDED.scores, action = EXCLUDED.action,
                                      fallback_used = EXCLUDED.fallback_used,
                                      latency_ms = EXCLUDED.latency_ms
                        RETURNING id
                        """,
                        (
                            eval_run_id,
                            project_id,
                            run_row["external_id"],
                            run_row["ticket_id"],
                            result.get("action"),
                            json.dumps(result),
                            bool(correctness.get("passed", True)),
                            json.dumps(correctness),
                            bool(result.get("fallback_used", False)),
                            result.get("wall_clock_ms"),
                        ),
                    )
                ).fetchone()
                if not correctness.get("passed", True):
                    if not correctness.get("policy", {}).get("passed", True):
                        layer = "policy"
                    elif correctness.get("golden") and not correctness["golden"].get("passed", True):
                        layer = "golden"
                    else:
                        layer = "structural"
                    category = result.get("failure_category") or (
                        "policy_violation"
                        if layer == "policy"
                        else next(
                            (
                                name
                                for name, passed in correctness.get("structural", {}).get("scores", {}).items()
                                if not passed
                            ),
                            "structural_failure",
                        )
                    )
                    await conn.execute(
                        """
                        INSERT INTO loopie.failures
                            (id, project_id, eval_case_result_id, category, layer, diagnosis, status)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'open')
                        ON CONFLICT (eval_case_result_id) DO UPDATE
                        SET category = EXCLUDED.category, layer = EXCLUDED.layer,
                            diagnosis = EXCLUDED.diagnosis, status = 'open', updated_at = NOW()
                        """,
                        (
                            str(uuid.uuid4()),
                            project_id,
                            eval_result["id"],
                            category,
                            layer,
                            json.dumps(
                                {
                                    **correctness,
                                    "classification": result.get("failure_classification"),
                                }
                            ),
                        ),
                    )
                elif proof and proof["improvement_proven"]:
                    await conn.execute(
                        """
                        UPDATE loopie.failures AS failure
                        SET status = 'corrected', updated_at = NOW()
                        FROM loopie.corrections AS correction
                        WHERE correction.id = %s AND failure.id = correction.failure_id
                        """,
                        (run_row["correction_id"],),
                    )

    async def fail_run(self, run_id: str, error: str) -> None:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                run = await (
                    await conn.execute(
                        """
                        UPDATE loopie.runs
                        SET status = 'failed', error = %s, finished_at = NOW()
                        WHERE id = %s
                        RETURNING project_id, ticket_id, mode
                        """,
                        (error[:2000], run_id),
                    )
                ).fetchone()
                if run is None:
                    raise KeyError(f"Unknown run {run_id}")
                ticket = await (
                    await conn.execute(
                        "SELECT external_id FROM loopie.tickets WHERE id = %s",
                        (run["ticket_id"],),
                    )
                ).fetchone()
                eval_run_id = f"continuous:{run_id}"
                summary = {
                    "passed": False,
                    "execution": {"passed": False, "error": error[:2000]},
                }
                await conn.execute(
                    """
                    INSERT INTO loopie.eval_runs
                        (id, project_id, run_id, label, suite_name, mode, summary)
                    VALUES (%s, %s, %s, 'continuous', 'continuous_ticket', %s, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary
                    """,
                    (
                        eval_run_id,
                        run["project_id"],
                        run_id,
                        run["mode"],
                        json.dumps(summary),
                    ),
                )
                eval_result = await (
                    await conn.execute(
                        """
                        INSERT INTO loopie.eval_case_results
                            (eval_run_id, project_id, case_id, ticket_id, decision, passed, scores)
                        VALUES (%s, %s, %s, %s, %s::jsonb, FALSE, %s::jsonb)
                        ON CONFLICT (eval_run_id, ticket_id) WHERE ticket_id IS NOT NULL
                        DO UPDATE SET decision = EXCLUDED.decision, passed = FALSE,
                                      scores = EXCLUDED.scores
                        RETURNING id
                        """,
                        (
                            eval_run_id,
                            run["project_id"],
                            ticket["external_id"],
                            run["ticket_id"],
                            json.dumps({"error": error[:2000], "fallback_used": False}),
                            json.dumps({"execution_completed": False}),
                        ),
                    )
                ).fetchone()
                await conn.execute(
                    """
                    INSERT INTO loopie.failures
                        (id, project_id, eval_case_result_id, category, layer, diagnosis, status)
                    VALUES (%s, %s, %s, 'structural_failure', 'structural', %s::jsonb, 'open')
                    ON CONFLICT (eval_case_result_id) DO UPDATE
                    SET category = 'structural_failure', diagnosis = EXCLUDED.diagnosis,
                        status = 'open', updated_at = NOW()
                    """,
                    (
                        str(uuid.uuid4()),
                        run["project_id"],
                        eval_result["id"],
                        json.dumps(summary),
                    ),
                )


@dataclass
class MemoryProductRepository:
    tickets: dict[str, dict[str, Any]]
    runs: dict[str, dict[str, Any]]
    manifests: dict[str, RunManifest]
    jobs: dict[str, dict[str, Any]]

    def __init__(self) -> None:
        self.tickets = {}
        self.runs = {}
        self.manifests = {}
        self.jobs = {}
        self.failures: dict[str, dict[str, Any]] = {}
        self.triage_items: dict[str, dict[str, Any]] = {}
        self.golden_annotations: dict[str, dict[str, Any]] = {}

    async def get_project(self, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        if project_id != DEFAULT_PROJECT_ID:
            return None
        from src.loopie.decide import ALLOWED_ACTIONS

        return {
            "id": DEFAULT_PROJECT_ID,
            "slug": "default",
            "name": "Loopie Support Reliability",
            "scope": "refund/billing/security",
            "action_taxonomy": sorted(ALLOWED_ACTIONS),
            "settings": {},
        }

    async def create_ticket(self, *, external_id: str, subject: str, body: str, channel: str = "api", customer_ref: str | None = None, metadata: dict[str, Any] | None = None, tags: list[str] | None = None, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        existing = next((item for item in self.tickets.values() if item["project_id"] == project_id and item["external_id"] == external_id), None)
        ticket_id = existing["id"] if existing else str(uuid.uuid4())
        row = {
            "id": ticket_id,
            "project_id": project_id,
            "external_id": external_id,
            "version": int(existing["version"]) + 1 if existing else 1,
            "subject": subject,
            "body": body,
            "channel": channel,
            "customer_ref": customer_ref,
            "metadata": metadata or {},
            "tags": tags or [],
            "created_at": existing["created_at"] if existing else _utcnow(),
            "updated_at": _utcnow(),
        }
        self.tickets[ticket_id] = row
        return dict(row)

    async def list_tickets(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.tickets.values() if row["project_id"] == project_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]

    async def get_ticket(self, ticket_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        row = self.tickets.get(ticket_id)
        return dict(row) if row and row["project_id"] == project_id else None

    async def get_golden_annotation(
        self,
        ticket_id: str,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> dict[str, Any] | None:
        row = self.golden_annotations.get(ticket_id)
        return dict(row) if row and row["project_id"] == project_id else None

    async def queue_run(self, *, ticket: dict[str, Any], manifest: RunManifest, mode: str, kind: str = "ticket", idempotency_key: str, parent_run_id: str | None = None, correction_id: str | None = None, project_id: str = DEFAULT_PROJECT_ID) -> tuple[dict[str, Any], dict[str, Any]]:
        existing = next((run for run in self.runs.values() if run["project_id"] == project_id and run["idempotency_key"] == idempotency_key), None)
        if existing:
            job = next(job for job in self.jobs.values() if job["payload"]["run_id"] == existing["id"])
            return dict(existing), dict(job)
        run_id = str(uuid.uuid4())
        job_id = str(uuid.uuid5(uuid.UUID(run_id), "execute"))
        self.manifests[manifest.id] = manifest
        run = {
            "id": run_id,
            "project_id": project_id,
            "idempotency_key": idempotency_key,
            "kind": kind,
            "mode": mode,
            "status": "queued",
            "ticket_id": ticket["id"],
            "parent_run_id": parent_run_id,
            "correction_id": correction_id,
            "manifest_id": manifest.id,
            "decision": None,
            "created_at": _utcnow(),
        }
        job = {
            "id": job_id,
            "project_id": project_id,
            "type": "execute_run",
            "payload": {"run_id": run_id},
            "status": "queued",
            "idempotency_key": f"run:{run_id}",
            "created_at": _utcnow(),
        }
        self.runs[run_id] = run
        self.jobs[job_id] = job
        return dict(run), dict(job)

    async def get_run(self, run_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        return dict(row) if row and row["project_id"] == project_id else None

    async def list_runs(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.runs.values() if row["project_id"] == project_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]

    async def list_failures(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.failures.values() if row["project_id"] == project_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]

    async def get_failure(self, failure_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any] | None:
        failure = self.failures.get(failure_id)
        if failure is None or failure["project_id"] != project_id:
            return None
        run = self.runs[failure["run_id"]]
        ticket = self.tickets[failure["ticket_id"]]
        return {**ticket, **failure, "ticket_id": ticket["id"], "run_id": run["id"], "mode": run["mode"], "kind": run["kind"], "decision": run["decision"]}

    async def tickets_affected_by_artifact(self, artifact_key: str, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        ticket_ids = {
            run["ticket_id"]
            for run in self.runs.values()
            if run["project_id"] == project_id
            and any(read["key"] == artifact_key for read in run.get("read_set", []))
        }
        return [dict(self.tickets[ticket_id]) for ticket_id in list(ticket_ids)[:limit]]

    async def list_triage_items(self, *, project_id: str = DEFAULT_PROJECT_ID, limit: int = 100) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.triage_items.values() if row["project_id"] == project_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]

    async def create_triage_item(self, *, run_id: str, verdict: dict[str, Any], confidence: float, calibration_sample: bool, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "run_id": run_id,
            "judge_verdict": verdict,
            "confidence": confidence,
            "status": "open",
            "calibration_sample": calibration_sample,
            "created_at": _utcnow(),
        }
        self.triage_items[row["id"]] = row
        return dict(row)

    async def judge_calibration(self, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        return {"samples": 0, "agreement": 0.0, "calibrated": False}

    async def resolve_triage_item(self, item_id: str, *, decision: str, actor: str, expected_action: str | None = None, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
        item = self.triage_items.get(item_id)
        if item is None or item["project_id"] != project_id:
            raise KeyError(f"Unknown triage item {item_id}")
        if decision == "confirm" and not expected_action:
            raise ValueError("expected_action is required when confirming")
        if decision == "confirm":
            run = self.runs[item["run_id"]]
            self.golden_annotations[run["ticket_id"]] = {
                "project_id": project_id,
                "ticket_id": run["ticket_id"],
                "expected_action": expected_action,
                "source": "human_triage",
                "annotated_by": actor,
            }
        item.update(
            status="confirmed" if decision == "confirm" else "rejected",
            resolution="promoted_to_golden" if decision == "confirm" else "dismissed",
            resolved_by=actor,
            resolved_at=_utcnow(),
        )
        return dict(item)

    async def get_run_manifest(self, manifest_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> RunManifest | None:
        manifest = self.manifests.get(manifest_id)
        return manifest if manifest and manifest.project_id == project_id else None

    async def mark_run_running(self, run_id: str) -> None:
        self.runs[run_id].update(status="running", started_at=_utcnow(), error=None)

    async def mark_run_queued(self, run_id: str, error: str) -> None:
        self.runs[run_id].update(status="queued", error=error[:2000])

    async def finish_run(self, run_id: str, result: dict[str, Any]) -> None:
        run = self.runs[run_id]
        parent = self.runs.get(str(run.get("parent_run_id"))) if run.get("parent_run_id") else None
        proof = _attach_improvement_proof(
            result,
            parent_decision=parent.get("decision") if parent else None,
            parent_run_id=run.get("parent_run_id"),
            correction_id=run.get("correction_id"),
        )
        self.runs[run_id].update(status="succeeded", decision=result, read_set=result["read_set"], finished_at=_utcnow(), error=None)
        correctness = result.get("correctness") or {"passed": True}
        if not correctness.get("passed", True):
            if not correctness.get("policy", {}).get("passed", True):
                layer = "policy"
            elif correctness.get("golden") and not correctness["golden"].get("passed", True):
                layer = "golden"
            else:
                layer = "structural"
            failure_id = str(uuid.uuid5(uuid.UUID(run_id), "failure"))
            self.failures[failure_id] = {
                "id": failure_id,
                "project_id": self.runs[run_id]["project_id"],
                "run_id": run_id,
                "ticket_id": self.runs[run_id]["ticket_id"],
                "category": result.get("failure_category") or (
                    "policy_violation" if layer == "policy" else "structural_failure"
                ),
                "layer": layer,
                "diagnosis": {
                    **correctness,
                    "classification": result.get("failure_classification"),
                },
                "status": "open",
                "created_at": _utcnow(),
            }
        elif proof and proof["improvement_proven"]:
            correction_id = run.get("correction_id")
            for failure in self.failures.values():
                if failure.get("correction_id") == correction_id or failure.get("run_id") == run.get("parent_run_id"):
                    failure.update(status="corrected", updated_at=_utcnow())

    async def fail_run(self, run_id: str, error: str) -> None:
        self.runs[run_id].update(status="failed", error=error[:2000], finished_at=_utcnow())
        failure_id = str(uuid.uuid5(uuid.UUID(run_id), "execution-failure"))
        self.failures[failure_id] = {
            "id": failure_id,
            "project_id": self.runs[run_id]["project_id"],
            "run_id": run_id,
            "ticket_id": self.runs[run_id]["ticket_id"],
            "category": "structural_failure",
            "layer": "structural",
            "diagnosis": {
                "passed": False,
                "execution": {"passed": False, "error": error[:2000]},
            },
            "status": "open",
            "created_at": _utcnow(),
        }
