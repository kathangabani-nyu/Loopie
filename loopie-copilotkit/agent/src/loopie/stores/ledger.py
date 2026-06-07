"""Postgres artifact Time Machine and cost ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.loopie.config import get_settings


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

    @classmethod
    def connect(cls, url: str | None = None, *, strict: bool | None = None) -> Ledger:
        settings = get_settings()
        require_postgres = strict if strict is not None else settings.requires_durable_stores
        ledger = cls(url=url or settings.postgres_url, _memory_rows=[], _memory_costs=[])
        ledger.ensure_schema()
        if require_postgres and not ledger.ping():
            raise RuntimeError(
                "Postgres is unreachable — hosted Loopie requires durable artifact audit storage. "
                "Set POSTGRES_URL or use LOOPIE_PERSISTENCE_MODE=memory for local dev only."
            )
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
    ) -> None:
        row = {
            "artifact_key": artifact_key,
            "version": version,
            "value": value,
            "source_case": source_case,
            "correction_id": correction_id,
            "status": status,
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO loopie.artifact_versions
                    (artifact_key, version, value, source_case, correction_id, status)
                    VALUES (%(artifact_key)s, %(version)s, %(value)s::jsonb,
                            %(source_case)s, %(correction_id)s, %(status)s)
                    ON CONFLICT (artifact_key, version) DO NOTHING
                    """,
                    {**row, "value": json.dumps(value)},
                )
                conn.commit()
        except Exception:
            # Mirror the Postgres UNIQUE(artifact_key, version) constraint so repeated
            # seeds in the in-memory fallback don't create duplicate rows.
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
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    TRUNCATE loopie.artifact_versions, loopie.corrections,
                             loopie.eval_runs, loopie.eval_case_results,
                             loopie.approval_events, loopie.audit_events
                    RESTART IDENTITY CASCADE
                    """
                )
                conn.execute("DELETE FROM loopie.cost_ledger WHERE mode <> 'chat'")
                conn.commit()
        except Exception:
            pass

    def record_cost(
        self,
        *,
        run_id: str,
        provider: str | None = None,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated_cost: float,
        stop_reason: str,
        mode: str,
    ) -> None:
        row = {
            "run_id": run_id,
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
                    (run_id, provider, model, prompt_tokens, completion_tokens, total_tokens,
                     estimated_cost, stop_reason, mode)
                    VALUES (%(run_id)s, %(provider)s, %(model)s, %(prompt_tokens)s, %(completion_tokens)s,
                            %(total_tokens)s, %(estimated_cost)s, %(stop_reason)s, %(mode)s)
                    """,
                    row,
                )
                conn.commit()
        except Exception:
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

    def artifact_history(self, artifact_key: str) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT artifact_key, version, value, created_at, source_case, correction_id, status
                    FROM loopie.artifact_versions
                    WHERE artifact_key = %s
                    ORDER BY version ASC
                    """,
                    (artifact_key,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return [r for r in self._memory_rows if r["artifact_key"] == artifact_key]

    def record_audit(self, event_type: str, payload: dict[str, Any]) -> int | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO loopie.audit_events (event_type, payload)
                    VALUES (%s, %s::jsonb)
                    RETURNING id
                    """,
                    (event_type, json.dumps(payload)),
                ).fetchone()
                conn.commit()
                return int(row["id"]) if row else None
        except Exception:
            return None

    def rollback(self, artifact_key: str, version: int) -> dict[str, Any] | None:
        history = self.artifact_history(artifact_key)
        target = next((row for row in history if row["version"] == version), None)
        if target is None:
            return None
        self.append_artifact_version(
            artifact_key=artifact_key,
            version=max((row["version"] for row in history), default=version) + 1,
            value=target["value"] if isinstance(target["value"], dict) else json.loads(target["value"]),
            source_case=f"rollback_to_v{version}",
            status="rollback",
        )
        self.record_audit("rollback", {"artifact_key": artifact_key, "version": version})
        return target
