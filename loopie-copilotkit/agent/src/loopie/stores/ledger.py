"""Postgres artifact Time Machine and cost ledger."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.loopie.config import get_settings
from src.loopie.manifests import DEFAULT_PROJECT_ID


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS loopie;

CREATE TABLE IF NOT EXISTS loopie.artifact_versions (
    id SERIAL PRIMARY KEY,
    artifact_key TEXT NOT NULL,
    version INT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_case TEXT,
    correction_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE (artifact_key, version)
);

CREATE TABLE IF NOT EXISTS loopie.cost_ledger (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_tokens INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    estimated_cost NUMERIC(12, 6) NOT NULL DEFAULT 0,
    stop_reason TEXT,
    mode TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loopie.corrections (
    id TEXT PRIMARY KEY,
    failure_case TEXT,
    category TEXT,
    proposal JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loopie.eval_runs (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loopie.eval_case_results (
    id SERIAL PRIMARY KEY,
    eval_run_id TEXT NOT NULL REFERENCES loopie.eval_runs(id),
    case_id TEXT NOT NULL,
    action TEXT,
    expected_action TEXT,
    passed BOOLEAN NOT NULL,
    scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loopie.approval_events (
    id SERIAL PRIMARY KEY,
    correction_id TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'human',
    decision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loopie.audit_events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass
class Ledger:
    url: str
    _memory_rows: list[dict[str, Any]]
    _memory_costs: list[dict[str, Any]]
    _postgres_ok: bool = False
    _memory_corrections: dict[str, dict[str, Any]] = field(default_factory=dict)
    _memory_outbox: list[dict[str, Any]] = field(default_factory=list)
    _memory_approvals: list[dict[str, Any]] = field(default_factory=list)
    _memory_lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def connect(cls, url: str | None = None, *, strict: bool | None = None) -> Ledger:
        settings = get_settings()
        ledger = cls(url=url or settings.postgres_url, _memory_rows=[], _memory_costs=[])
        if settings.persistence_mode == "memory" and strict is not True:
            return ledger
        if not ledger.ping():
            raise RuntimeError(
                "Postgres is unreachable — hosted Loopie requires durable artifact audit storage. "
                "Set POSTGRES_URL or use LOOPIE_PERSISTENCE_MODE=memory for local dev only."
            )
        try:
            with ledger._connect() as conn:
                row = conn.execute(
                    "SELECT to_regclass('loopie.run_manifests') AS table_name"
                ).fetchone()
                if not row or row["table_name"] is None:
                    raise RuntimeError("Loopie migrations are not applied; run `alembic upgrade head`")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Unable to verify the Loopie migration baseline") from exc
        return ledger

    @property
    def persistence_mode(self) -> str:
        return "postgres" if self._postgres_ok else "memory"

    def ping(self) -> bool:
        try:
            with psycopg.connect(self.url) as conn:
                conn.execute("SELECT 1")
            self._postgres_ok = True
            return True
        except Exception:
            self._postgres_ok = False
            return False

    def ensure_schema(self) -> None:
        try:
            with psycopg.connect(self.url) as conn:
                conn.execute(SCHEMA_SQL)
                conn.commit()
            self._postgres_ok = True
        except Exception:
            self._postgres_ok = False

    def _connect(self):
        if get_settings().persistence_mode == "memory" and not self._postgres_ok:
            raise RuntimeError("Postgres access is disabled in explicit memory persistence mode")
        return psycopg.connect(self.url, row_factory=dict_row)

    def append_artifact_version(
        self,
        *,
        artifact_key: str,
        version: int,
        value: dict[str, Any],
        source_case: str | None = None,
        correction_id: str | None = None,
        status: str = "active",
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> None:
        row = {
            "artifact_key": artifact_key,
            "version": version,
            "value": value,
            "source_case": source_case,
            "correction_id": correction_id,
            "status": status,
            "project_id": project_id,
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO loopie.artifact_versions
                    (project_id, artifact_key, version, value, source_case, correction_id, status)
                    VALUES (%(project_id)s, %(artifact_key)s, %(version)s, %(value)s::jsonb,
                            %(source_case)s, %(correction_id)s, %(status)s)
                    ON CONFLICT (project_id, artifact_key, version) DO NOTHING
                    """,
                    {**row, "value": json.dumps(value)},
                )
                conn.commit()
        except (KeyError, ValueError):
            raise
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            # Mirror the Postgres UNIQUE(project_id, artifact_key, version) constraint
            # so repeated seeds in the in-memory fallback don't create duplicate rows.
            already = any(
                r["artifact_key"] == artifact_key and r["version"] == version
                for r in self._memory_rows
            )
            if not already:
                self._memory_rows.append(row)

    def reset(self) -> None:
        """Clear Loopie demo ledger state while preserving unrelated chat spend history."""
        chat_costs = [row for row in self._memory_costs if row.get("mode") == "chat"]
        self._memory_rows.clear()
        self._memory_costs = chat_costs
        self._memory_corrections.clear()
        self._memory_outbox.clear()
        self._memory_approvals.clear()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    TRUNCATE loopie.artifact_outbox, loopie.approvals,
                             loopie.failures, loopie.triage_items,
                             loopie.eval_case_results, loopie.eval_runs,
                             loopie.run_read_sets, loopie.jobs, loopie.runs,
                             loopie.run_manifests, loopie.corrections,
                             loopie.artifact_versions, loopie.approval_events,
                             loopie.audit_events
                    RESTART IDENTITY CASCADE
                    """
                )
                conn.execute("DELETE FROM loopie.cost_ledger WHERE mode <> 'chat'")
                conn.commit()
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise

    def record_cost(
        self,
        *,
        run_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated_cost: float,
        stop_reason: str,
        mode: str,
        provider: str | None = None,
        project_id: str = DEFAULT_PROJECT_ID,
        run_uuid: str | None = None,
    ) -> None:
        row = {
            "run_id": run_id,
            "project_id": project_id,
            "run_uuid": run_uuid,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost": estimated_cost,
            "stop_reason": stop_reason,
            "mode": mode,
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO loopie.cost_ledger
                    (project_id, run_uuid, run_id, provider, model, prompt_tokens, completion_tokens, total_tokens,
                     estimated_cost, stop_reason, mode)
                    VALUES (%(project_id)s, %(run_uuid)s, %(run_id)s, %(provider)s, %(model)s, %(prompt_tokens)s, %(completion_tokens)s,
                            %(total_tokens)s, %(estimated_cost)s, %(stop_reason)s, %(mode)s)
                    """,
                    row,
                )
                conn.commit()
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            self._memory_costs.append(row)

    def cost_by_provider(self) -> dict[str, float]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT COALESCE(provider, 'unknown') AS provider,
                           COALESCE(SUM(estimated_cost), 0) AS total
                    FROM loopie.cost_ledger
                    GROUP BY provider
                    """
                ).fetchall()
                return {str(r["provider"]): float(r["total"]) for r in rows}
        except Exception:
            totals: dict[str, float] = {}
            for row in self._memory_costs:
                key = str(row.get("provider") or "unknown")
                totals[key] = totals.get(key, 0.0) + float(row.get("estimated_cost", 0))
            return totals

    def total_cost(self, *, mode: str | None = None) -> float:
        try:
            with self._connect() as conn:
                if mode:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM loopie.cost_ledger WHERE mode = %s",
                        (mode,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM loopie.cost_ledger"
                    ).fetchone()
                return float(row["total"] if row else 0)
        except Exception:
            rows = self._memory_costs if mode is None else [r for r in self._memory_costs if r["mode"] == mode]
            return float(sum(r["estimated_cost"] for r in rows))

    def artifact_history(
        self,
        artifact_key: str,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT artifact_key, version, value, created_at, source_case, correction_id, status
                    FROM loopie.artifact_versions
                    WHERE project_id = %s AND artifact_key = %s
                    ORDER BY version ASC
                    """,
                    (project_id, artifact_key),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return [r for r in self._memory_rows if r["artifact_key"] == artifact_key]

    def record_audit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        project_id: str = DEFAULT_PROJECT_ID,
        run_id: str | None = None,
    ) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO loopie.audit_events (project_id, run_id, event_type, payload)
                    VALUES (%s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (project_id, run_id, event_type, json.dumps(payload)),
                ).fetchone()
                conn.commit()
                if row is None:
                    raise RuntimeError("Audit event insert returned no receipt")
                return int(row["id"])
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            return 1

    def register_correction(
        self,
        correction: dict[str, Any],
        *,
        artifact_key: str,
        base_artifact_version: int,
        shadow_passed: bool,
        shadow_eval_run_id: str | None = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> None:
        row = {
            **correction,
            "artifact_key": artifact_key,
            "base_artifact_version": str(base_artifact_version),
            "shadow_passed": shadow_passed,
            "shadow_eval_run_id": shadow_eval_run_id,
            "project_id": project_id,
            "status": "proposed" if shadow_passed else "shadow_failed",
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO loopie.corrections
                        (id, project_id, failure_id, failure_case, category, proposal, kind, payload,
                         base_artifact_version, diff, blast_radius, shadow_eval_run_id,
                         shadow_passed, status, proposed_by, model, idempotency_key)
                    VALUES
                        (%(id)s, %(project_id)s, %(failure_id)s, %(case_id)s, %(category)s, %(proposal)s::jsonb,
                         %(type)s, %(payload)s::jsonb, %(base_artifact_version)s,
                         %(diff)s::jsonb, %(blast_radius)s::jsonb, %(shadow_eval_run_id)s,
                         %(shadow_passed)s, %(status)s, %(proposed_by)s, %(model)s,
                         %(idempotency_key)s)
                    ON CONFLICT (id) DO UPDATE SET
                        failure_id = EXCLUDED.failure_id,
                        failure_case = EXCLUDED.failure_case,
                        category = EXCLUDED.category,
                        proposal = EXCLUDED.proposal,
                        kind = EXCLUDED.kind,
                        payload = EXCLUDED.payload,
                        base_artifact_version = EXCLUDED.base_artifact_version,
                        diff = EXCLUDED.diff,
                        blast_radius = EXCLUDED.blast_radius,
                        shadow_eval_run_id = EXCLUDED.shadow_eval_run_id,
                        shadow_passed = EXCLUDED.shadow_passed,
                        status = EXCLUDED.status,
                        proposed_by = EXCLUDED.proposed_by,
                        model = EXCLUDED.model
                    WHERE loopie.corrections.status IN ('proposed', 'shadow_failed')
                    """,
                    {
                        **row,
                        "proposal": json.dumps(correction.get("proposal", {})),
                        "failure_id": correction.get("failure_id"),
                        "payload": json.dumps(
                            {
                                "artifact_key": artifact_key,
                                "value": correction.get("candidate_value"),
                                "summary": correction.get("summary"),
                            }
                        ),
                        "diff": json.dumps(correction.get("diff", [])),
                        "blast_radius": json.dumps(correction.get("blast_radius", {})),
                        "proposed_by": correction.get("proposed_by", "test_fixture"),
                        "model": correction.get("model"),
                        "idempotency_key": correction.get("idempotency_key", correction["id"]),
                    },
                )
                if correction.get("failure_id"):
                    conn.execute(
                        "UPDATE loopie.failures SET status = %s, updated_at = NOW() WHERE id = %s",
                        ("proposed" if shadow_passed else "open", correction["failure_id"]),
                    )
                conn.commit()
            return
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
        with self._memory_lock:
            self._memory_corrections.setdefault(correction["id"], row)

    def commit_correction(
        self,
        correction_id: str,
        *,
        actor: str = "human",
        channel: str = "ui",
        note: str | None = None,
    ) -> dict[str, Any]:
        """CAS + approval + artifact version + outbox in one transaction."""
        try:
            with self._connect() as conn:
                with conn.transaction():
                    correction = conn.execute(
                        "SELECT * FROM loopie.corrections WHERE id = %s FOR UPDATE",
                        (correction_id,),
                    ).fetchone()
                    if correction is None:
                        raise KeyError(f"Unknown correction {correction_id}")
                    if correction["status"] == "applied":
                        existing = conn.execute(
                            "SELECT * FROM loopie.artifact_versions WHERE correction_id = %s",
                            (correction_id,),
                        ).fetchone()
                        return {**dict(existing), "no_op": True} if existing else {"correction_id": correction_id, "no_op": True}
                    if correction["status"] != "proposed" or not correction["shadow_passed"]:
                        raise ValueError("correction must be proposed with a passing shadow evaluation")
                    payload = dict(correction["payload"] or {})
                    artifact_key = str(payload["artifact_key"])
                    latest = conn.execute(
                        """
                        SELECT * FROM loopie.artifact_versions
                        WHERE project_id = %s AND artifact_key = %s
                        ORDER BY version DESC LIMIT 1 FOR UPDATE
                        """,
                        (correction["project_id"], artifact_key),
                    ).fetchone()
                    current_version = int(latest["version"]) if latest else 0
                    if str(current_version) != str(correction["base_artifact_version"]):
                        raise ValueError(
                            f"artifact CAS conflict: expected v{correction['base_artifact_version']}, current v{current_version}"
                        )
                    new_value = payload["value"]
                    before_value = latest["value"] if latest else None
                    conn.execute(
                        """
                        INSERT INTO loopie.approvals
                            (id, project_id, correction_id, decision, actor, channel, note)
                        VALUES (%s, %s, %s, 'approved', %s, %s, %s)
                        """,
                        (
                            str(uuid.uuid4()),
                            correction["project_id"],
                            correction_id,
                            actor,
                            channel,
                            note,
                        ),
                    )
                    if before_value == new_value:
                        conn.execute("UPDATE loopie.corrections SET status = 'applied' WHERE id = %s", (correction_id,))
                        return {
                            "artifact_key": artifact_key,
                            "version": current_version,
                            "correction_id": correction_id,
                            "no_op": True,
                        }
                    version = current_version + 1
                    conn.execute(
                        """
                        INSERT INTO loopie.artifact_versions
                            (project_id, artifact_key, version, value, source_case, correction_id, status)
                        VALUES (%s, %s, %s, %s::jsonb, %s, %s, 'active')
                        """,
                        (
                            correction["project_id"],
                            artifact_key,
                            version,
                            json.dumps(new_value),
                            correction["failure_case"],
                            correction_id,
                        ),
                    )
                    conn.execute("UPDATE loopie.corrections SET status = 'applied' WHERE id = %s", (correction_id,))
                    outbox_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO loopie.artifact_outbox
                            (id, project_id, correction_id, artifact_key, version, value)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            outbox_id,
                            correction["project_id"],
                            correction_id,
                            artifact_key,
                            version,
                            json.dumps(new_value),
                        ),
                    )
                    return {
                        "artifact_key": artifact_key,
                        "version": version,
                        "value": new_value,
                        "before_value": before_value,
                        "correction_id": correction_id,
                        "outbox_id": outbox_id,
                        "no_op": False,
                    }
        except (KeyError, ValueError):
            raise
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            return self._commit_correction_memory(
                correction_id,
                actor=actor,
                channel=channel,
                note=note,
            )

    def _commit_correction_memory(
        self,
        correction_id: str,
        *,
        actor: str,
        channel: str,
        note: str | None,
    ) -> dict[str, Any]:
        with self._memory_lock:
            correction = self._memory_corrections.get(correction_id)
            if correction is None:
                raise KeyError(f"Unknown correction {correction_id}")
            if correction["status"] == "applied":
                existing = next((row for row in self._memory_rows if row.get("correction_id") == correction_id), None)
                return {**(existing or {}), "correction_id": correction_id, "no_op": True}
            if correction["status"] != "proposed" or not correction["shadow_passed"]:
                raise ValueError("correction must be proposed with a passing shadow evaluation")
            artifact_key = correction["artifact_key"]
            history = [row for row in self._memory_rows if row["artifact_key"] == artifact_key]
            latest = max(history, key=lambda row: row["version"]) if history else None
            current_version = int(latest["version"]) if latest else 0
            if str(current_version) != str(correction["base_artifact_version"]):
                raise ValueError(
                    f"artifact CAS conflict: expected v{correction['base_artifact_version']}, current v{current_version}"
                )
            new_value = correction["candidate_value"]
            before_value = latest["value"] if latest else None
            correction["status"] = "applied"
            self._memory_approvals.append(
                {
                    "correction_id": correction_id,
                    "decision": "approved",
                    "actor": actor,
                    "channel": channel,
                    "note": note,
                }
            )
            if before_value == new_value:
                return {
                    "artifact_key": artifact_key,
                    "version": current_version,
                    "correction_id": correction_id,
                    "no_op": True,
                }
            version = current_version + 1
            row = {
                "artifact_key": artifact_key,
                "version": version,
                "value": new_value,
                "source_case": correction.get("case_id"),
                "correction_id": correction_id,
                "status": "active",
            }
            self._memory_rows.append(row)
            outbox = {
                "id": str(uuid.uuid4()),
                "project_id": correction["project_id"],
                "correction_id": correction_id,
                "artifact_key": artifact_key,
                "version": version,
                "value": new_value,
                "projected_at": None,
            }
            self._memory_outbox.append(outbox)
            return {
                **row,
                "before_value": before_value,
                "outbox_id": outbox["id"],
                "no_op": False,
            }

    def pending_outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM loopie.artifact_outbox
                    WHERE projected_at IS NULL
                    ORDER BY committed_at, id
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            return [dict(row) for row in self._memory_outbox if row["projected_at"] is None][:limit]

    def mark_outbox_projected(self, outbox_id: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE loopie.artifact_outbox SET projected_at = NOW(), attempts = attempts + 1 WHERE id = %s",
                    (outbox_id,),
                )
                conn.commit()
            return
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
        with self._memory_lock:
            for row in self._memory_outbox:
                if row["id"] == outbox_id:
                    row["projected_at"] = "now"
                    break

    def list_corrections(self, *, limit: int = 100) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM loopie.corrections ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            return list(reversed([dict(row) for row in self._memory_corrections.values()]))[:limit]

    def get_correction(
        self,
        correction_id: str,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM loopie.corrections WHERE id = %s AND project_id = %s",
                    (correction_id, project_id),
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise
            row = self._memory_corrections.get(correction_id)
            return dict(row) if row and row.get("project_id") == project_id else None

    def reject_correction(
        self,
        correction_id: str,
        *,
        actor: str,
        channel: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record a durable human rejection without touching artifact state."""

        try:
            with self._connect() as conn:
                with conn.transaction():
                    correction = conn.execute(
                        "SELECT * FROM loopie.corrections WHERE id = %s FOR UPDATE",
                        (correction_id,),
                    ).fetchone()
                    if correction is None:
                        raise KeyError(f"Unknown correction {correction_id}")
                    if correction["status"] == "rejected":
                        return {"correction_id": correction_id, "status": "rejected", "no_op": True}
                    if correction["status"] != "proposed":
                        raise ValueError(
                            f"only a proposed correction can be rejected; current status is {correction['status']}"
                        )
                    conn.execute(
                        """
                        INSERT INTO loopie.approvals
                            (id, project_id, correction_id, decision, actor, channel, note)
                        VALUES (%s, %s, %s, 'rejected', %s, %s, %s)
                        """,
                        (
                            str(uuid.uuid4()),
                            correction["project_id"],
                            correction_id,
                            actor,
                            channel,
                            note,
                        ),
                    )
                    conn.execute(
                        "UPDATE loopie.corrections SET status = 'rejected' WHERE id = %s",
                        (correction_id,),
                    )
                    return {"correction_id": correction_id, "status": "rejected", "no_op": False}
        except (KeyError, ValueError):
            raise
        except Exception:
            if self._postgres_ok or get_settings().requires_durable_stores:
                raise

        with self._memory_lock:
            correction = self._memory_corrections.get(correction_id)
            if correction is None:
                raise KeyError(f"Unknown correction {correction_id}")
            if correction["status"] == "rejected":
                return {"correction_id": correction_id, "status": "rejected", "no_op": True}
            if correction["status"] != "proposed":
                raise ValueError(
                    f"only a proposed correction can be rejected; current status is {correction['status']}"
                )
            correction["status"] = "rejected"
            self._memory_approvals.append(
                {
                    "correction_id": correction_id,
                    "decision": "rejected",
                    "actor": actor,
                    "channel": channel,
                    "note": note,
                }
            )
            return {"correction_id": correction_id, "status": "rejected", "no_op": False}
