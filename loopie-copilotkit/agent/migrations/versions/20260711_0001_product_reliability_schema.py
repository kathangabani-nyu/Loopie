"""Product reliability schema and default-project seed.

Revision ID: 20260711_0001
Revises:
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = "20260711_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
ACTION_TAXONOMY = [
    "approve_refund",
    "ask_clarification",
    "block_refund_tool",
    "block_unauthorized_refund",
    "check_enterprise_override",
    "deny_refund_offer_credit",
    "escalate_after_loop",
    "escalate_billing_review",
    "escalate_manual_review",
    "escalate_security",
    "escalate_stuck_lookup",
    "require_fresh_policy_version",
    "require_security_review",
    "retry_policy_lookup",
]
SEED_DIR = Path(__file__).resolve().parents[1] / "seeds"
GOLDEN_FIELDS = {
    "expected_action",
    "failure_seed",
    "neighbors",
    "expected_failure_baseline",
    "expected_memory_version",
    "diagnosis_hint",
}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json(value: object) -> str:
    # SQLAlchemy TextClause recognizes ``:token`` even inside SQL string
    # literals. Escape colons so offline compilation cannot silently replace
    # numeric/boolean JSON values with NULL bind placeholders.
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).replace(":", r"\:")
    return _literal(encoded) + "::jsonb"


def _ddl(statement: str) -> None:
    op.execute(statement)


def _ticket_metadata(fixture: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in fixture.items()
        if key not in GOLDEN_FIELDS | {"case_id", "request"}
    }


def upgrade() -> None:
    _ddl("CREATE SCHEMA IF NOT EXISTS loopie")
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.projects (
            id UUID PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            scope TEXT NOT NULL,
            action_taxonomy JSONB NOT NULL,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        f"""
        INSERT INTO loopie.projects (id, slug, name, scope, action_taxonomy)
        VALUES ('{DEFAULT_PROJECT_ID}'::uuid, 'default', 'Loopie Support Reliability',
                'refund/billing/security', {_json(ACTION_TAXONOMY)})
        ON CONFLICT (id) DO NOTHING
        """
    )
    _ensure_legacy_tables()
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.tickets (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            external_id TEXT NOT NULL,
            version INT NOT NULL DEFAULT 1 CHECK (version > 0),
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'api',
            customer_ref TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            tags TEXT[] NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, external_id)
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.golden_annotations (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            ticket_id UUID NOT NULL UNIQUE REFERENCES loopie.tickets(id) ON DELETE CASCADE,
            expected_action TEXT NOT NULL,
            failure_seed TEXT,
            declared_neighbors JSONB NOT NULL DEFAULT '[]'::jsonb,
            expected_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            source TEXT NOT NULL CHECK (source IN ('fixture','human_triage','outcome_signal')),
            annotated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.policy_rules (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            rule_id TEXT NOT NULL,
            name TEXT NOT NULL,
            dsl JSONB NOT NULL,
            source_doc_ref TEXT,
            compiled_by_model TEXT,
            status TEXT NOT NULL CHECK (status IN ('proposed','approved','retired')),
            version INT NOT NULL CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, rule_id, version)
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.run_manifests (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            ticket_id UUID NOT NULL REFERENCES loopie.tickets(id),
            ticket_version INT NOT NULL CHECK (ticket_version > 0),
            artifact_contents JSONB NOT NULL,
            artifact_hashes JSONB NOT NULL,
            prompt_versions JSONB NOT NULL,
            schema_versions JSONB NOT NULL,
            model_config JSONB NOT NULL,
            tool_versions JSONB NOT NULL,
            code_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE OR REPLACE FUNCTION loopie.reject_run_manifest_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'run manifests are immutable';
        END $$
        """
    )
    _ddl("DROP TRIGGER IF EXISTS run_manifests_immutable ON loopie.run_manifests")
    _ddl(
        """
        CREATE TRIGGER run_manifests_immutable
        BEFORE UPDATE OR DELETE ON loopie.run_manifests
        FOR EACH ROW EXECUTE FUNCTION loopie.reject_run_manifest_mutation()
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.runs (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            idempotency_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
            ticket_id UUID NOT NULL REFERENCES loopie.tickets(id),
            parent_run_id UUID REFERENCES loopie.runs(id),
            correction_id TEXT REFERENCES loopie.corrections(id),
            manifest_id UUID REFERENCES loopie.run_manifests(id),
            decision JSONB,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, idempotency_key)
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.run_read_sets (
            run_id UUID NOT NULL REFERENCES loopie.runs(id) ON DELETE CASCADE,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            artifact_key TEXT NOT NULL,
            artifact_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (run_id, artifact_key)
        )
        """
    )
    _ddl("CREATE INDEX IF NOT EXISTS run_read_sets_artifact_idx ON loopie.run_read_sets(project_id, artifact_key, content_hash)")
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.jobs (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            type TEXT NOT NULL,
            payload JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
            attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            max_attempts INT NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
            idempotency_key TEXT NOT NULL,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            lease_owner TEXT,
            lease_token UUID,
            lease_expires_at TIMESTAMPTZ,
            heartbeat_at TIMESTAMPTZ,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, idempotency_key)
        )
        """
    )
    _ddl("CREATE INDEX IF NOT EXISTS jobs_claim_idx ON loopie.jobs(status, next_attempt_at, lease_expires_at)")

    _upgrade_legacy_tables()
    _create_reliability_tables()
    _seed_fixtures()
    _seed_policy_rules()


def _ensure_legacy_tables() -> None:
    """Create the demo tables on fresh databases before additive upgrades."""
    _ddl(
        """
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
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.cost_ledger (
            id SERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            prompt_tokens INT NOT NULL DEFAULT 0,
            completion_tokens INT NOT NULL DEFAULT 0,
            total_tokens INT NOT NULL DEFAULT 0,
            estimated_cost NUMERIC(12,6) NOT NULL DEFAULT 0,
            stop_reason TEXT,
            mode TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.corrections (
            id TEXT PRIMARY KEY,
            failure_case TEXT,
            category TEXT,
            proposal JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.eval_runs (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.eval_case_results (
            id SERIAL PRIMARY KEY,
            eval_run_id TEXT NOT NULL REFERENCES loopie.eval_runs(id),
            case_id TEXT NOT NULL,
            action TEXT,
            expected_action TEXT,
            passed BOOLEAN NOT NULL,
            scores JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.approval_events (
            id SERIAL PRIMARY KEY,
            correction_id TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'human',
            decision TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.audit_events (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _upgrade_legacy_tables() -> None:
    # These tables existed in the demo. Additive columns preserve its evidence
    # while new code migrates to UUID run foreign keys.
    _ddl("ALTER TABLE loopie.artifact_versions ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl(f"UPDATE loopie.artifact_versions SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.artifact_versions ALTER COLUMN project_id SET NOT NULL")
    _ddl("CREATE UNIQUE INDEX IF NOT EXISTS artifact_versions_project_version_idx ON loopie.artifact_versions(project_id, artifact_key, version)")

    _ddl("ALTER TABLE loopie.cost_ledger ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl("ALTER TABLE loopie.cost_ledger ADD COLUMN IF NOT EXISTS run_uuid UUID REFERENCES loopie.runs(id)")
    _ddl(f"UPDATE loopie.cost_ledger SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.cost_ledger ALTER COLUMN project_id SET NOT NULL")

    _ddl("ALTER TABLE loopie.audit_events ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl(f"UPDATE loopie.audit_events SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.audit_events ALTER COLUMN project_id SET NOT NULL")

    _ddl("ALTER TABLE loopie.eval_runs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl("ALTER TABLE loopie.eval_runs ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES loopie.runs(id)")
    _ddl("ALTER TABLE loopie.eval_runs ADD COLUMN IF NOT EXISTS suite_name TEXT")
    _ddl("ALTER TABLE loopie.eval_runs ADD COLUMN IF NOT EXISTS weave_url TEXT")
    _ddl("ALTER TABLE loopie.eval_runs ADD COLUMN IF NOT EXISTS summary JSONB NOT NULL DEFAULT '{}'::jsonb")
    _ddl(f"UPDATE loopie.eval_runs SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.eval_runs ALTER COLUMN project_id SET NOT NULL")

    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS ticket_id UUID REFERENCES loopie.tickets(id)")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS decision JSONB")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS failure_category TEXT")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS violated_rules TEXT[] NOT NULL DEFAULT '{}'")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS latency_ms NUMERIC(12,3)")
    _ddl("ALTER TABLE loopie.eval_case_results ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN NOT NULL DEFAULT FALSE")
    _ddl(f"UPDATE loopie.eval_case_results SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.eval_case_results ALTER COLUMN project_id SET NOT NULL")
    _ddl("CREATE UNIQUE INDEX IF NOT EXISTS eval_case_result_ticket_idx ON loopie.eval_case_results(eval_run_id, ticket_id) WHERE ticket_id IS NOT NULL")

    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES loopie.projects(id)")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS failure_id UUID")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS kind TEXT")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS payload JSONB")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS base_artifact_version TEXT")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS diff JSONB NOT NULL DEFAULT '{}'::jsonb")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS blast_radius JSONB NOT NULL DEFAULT '{}'::jsonb")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS shadow_eval_run_id TEXT")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS shadow_passed BOOLEAN NOT NULL DEFAULT FALSE")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS proposed_by TEXT")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS model TEXT")
    _ddl("ALTER TABLE loopie.corrections ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    _ddl(f"UPDATE loopie.corrections SET project_id = '{DEFAULT_PROJECT_ID}'::uuid WHERE project_id IS NULL")
    _ddl("ALTER TABLE loopie.corrections ALTER COLUMN project_id SET NOT NULL")
    _ddl("CREATE UNIQUE INDEX IF NOT EXISTS corrections_idempotency_idx ON loopie.corrections(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL")


def _create_reliability_tables() -> None:
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.failures (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            eval_case_result_id INT NOT NULL REFERENCES loopie.eval_case_results(id),
            category TEXT NOT NULL,
            layer TEXT NOT NULL CHECK (layer IN ('policy','structural','golden')),
            diagnosis JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL CHECK (status IN ('open','proposed','corrected','dismissed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl("CREATE UNIQUE INDEX IF NOT EXISTS failures_eval_case_idx ON loopie.failures(eval_case_result_id)")
    _ddl(
        """
        DO $$ BEGIN
            ALTER TABLE loopie.corrections
            ADD CONSTRAINT corrections_failure_fk
            FOREIGN KEY (failure_id) REFERENCES loopie.failures(id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.triage_items (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            run_id UUID NOT NULL REFERENCES loopie.runs(id),
            judge_verdict JSONB NOT NULL,
            confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL CHECK (status IN ('open','confirmed','rejected')),
            resolution TEXT CHECK (resolution IN ('promoted_to_golden','dismissed')),
            resolved_by TEXT,
            calibration_sample BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    _ddl("CREATE UNIQUE INDEX IF NOT EXISTS triage_run_idx ON loopie.triage_items(run_id)")
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.approvals (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            correction_id TEXT NOT NULL REFERENCES loopie.corrections(id),
            decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
            actor TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('hitl_chat','rest','ui')),
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _ddl(
        """
        CREATE TABLE IF NOT EXISTS loopie.artifact_outbox (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES loopie.projects(id),
            correction_id TEXT REFERENCES loopie.corrections(id),
            artifact_key TEXT NOT NULL,
            version INT NOT NULL CHECK (version > 0),
            value JSONB NOT NULL,
            committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            projected_at TIMESTAMPTZ,
            attempts INT NOT NULL DEFAULT 0,
            last_error TEXT,
            UNIQUE (project_id, artifact_key, version)
        )
        """
    )
    _ddl("CREATE INDEX IF NOT EXISTS artifact_outbox_pending_idx ON loopie.artifact_outbox(committed_at) WHERE projected_at IS NULL")


def _seed_fixtures() -> None:
    fixtures = [json.loads(line) for line in (SEED_DIR / "v2_tickets.jsonl").read_text(encoding="utf-8").splitlines() if line]
    namespace = uuid.UUID(DEFAULT_PROJECT_ID)
    for fixture in fixtures:
        ticket_id = uuid.uuid5(namespace, fixture["case_id"])
        metadata = _ticket_metadata(fixture)
        subject = fixture["request"][:120]
        tag = fixture["case_id"].split("_", 1)[0]
        _ddl(
            f"""
            INSERT INTO loopie.tickets
                (id, project_id, external_id, subject, body, channel, metadata, tags)
            VALUES
                ('{ticket_id}'::uuid, '{DEFAULT_PROJECT_ID}'::uuid, {_literal(fixture['case_id'])},
                 {_literal(subject)}, {_literal(fixture['request'])}, 'fixture', {_json(metadata)},
                 ARRAY[{_literal(tag)}]::text[])
            ON CONFLICT (project_id, external_id) DO NOTHING
            """
        )
        expected_metadata = {
            key: fixture[key]
            for key in ("expected_failure_baseline", "expected_memory_version", "diagnosis_hint")
            if key in fixture
        }
        annotation_id = uuid.uuid5(namespace, f"golden:{fixture['case_id']}")
        _ddl(
            f"""
            INSERT INTO loopie.golden_annotations
                (id, project_id, ticket_id, expected_action, failure_seed, declared_neighbors,
                 expected_metadata, source, annotated_by)
            VALUES
                ('{annotation_id}'::uuid, '{DEFAULT_PROJECT_ID}'::uuid, '{ticket_id}'::uuid,
                 {_literal(fixture['expected_action'])},
                 {(_literal(fixture['failure_seed']) if fixture.get('failure_seed') else 'NULL')},
                 {_json(fixture.get('neighbors', []))}, {_json(expected_metadata)}, 'fixture', 'migration:v2')
            ON CONFLICT (ticket_id) DO NOTHING
            """
        )


def _seed_policy_rules() -> None:
    namespace = uuid.UUID(DEFAULT_PROJECT_ID)
    rules = json.loads((SEED_DIR / "v2_policy_rules.json").read_text(encoding="utf-8"))
    for rule in rules:
        rule_pk = uuid.uuid5(namespace, f"policy:{rule['rule_id']}:{rule['version']}")
        _ddl(
            f"""
            INSERT INTO loopie.policy_rules
                (id, project_id, rule_id, name, dsl, source_doc_ref, compiled_by_model, status, version)
            VALUES
                ('{rule_pk}'::uuid, '{DEFAULT_PROJECT_ID}'::uuid, {_literal(rule['rule_id'])},
                 {_literal(rule['name'])}, {_json(rule)}, 'migration:v2', NULL,
                 {_literal(rule['status'])}, {int(rule['version'])})
            ON CONFLICT (project_id, rule_id, version) DO NOTHING
            """
        )


def downgrade() -> None:
    # The v2 baseline is deliberately additive over the demo ledger. Downgrade
    # removes new product tables but leaves upgraded legacy columns intact so
    # historical evidence is never destroyed by a rollback.
    _ddl("DROP TABLE IF EXISTS loopie.artifact_outbox")
    _ddl("DROP TABLE IF EXISTS loopie.approvals")
    _ddl("DROP TABLE IF EXISTS loopie.triage_items")
    _ddl("DROP TABLE IF EXISTS loopie.failures")
    _ddl("DROP TABLE IF EXISTS loopie.run_read_sets")
    _ddl("DROP TABLE IF EXISTS loopie.runs")
    _ddl("DROP TRIGGER IF EXISTS run_manifests_immutable ON loopie.run_manifests")
    _ddl("DROP FUNCTION IF EXISTS loopie.reject_run_manifest_mutation()")
    _ddl("DROP TABLE IF EXISTS loopie.run_manifests")
    _ddl("DROP TABLE IF EXISTS loopie.jobs")
    _ddl("DROP TABLE IF EXISTS loopie.policy_rules")
    _ddl("DROP TABLE IF EXISTS loopie.golden_annotations")
    _ddl("DROP TABLE IF EXISTS loopie.tickets")
    _ddl("DROP TABLE IF EXISTS loopie.projects")
