"""Pin ticket evidence and correlate durable run receipts.

Revision ID: 20260712_0003
Revises: 20260711_0002
"""

from __future__ import annotations

import json
import uuid
from typing import Sequence

from alembic import op

revision: str = "20260712_0003"
down_revision: str | Sequence[str] | None = "20260711_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
ACTION_TAXONOMY = [
    "approve_refund",
    "ask_clarification",
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


def upgrade() -> None:
    op.execute("ALTER TABLE loopie.tickets ADD COLUMN IF NOT EXISTS facts JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute(
        """
        UPDATE loopie.tickets
        SET facts = jsonb_strip_nulls(jsonb_build_object(
            'customer_tier', metadata->'customer_tier',
            'days_since_purchase', metadata->'days_since_purchase',
            'security_flag', metadata->'security_flag',
            'amount_minor', metadata->'amount_minor',
            'currency', metadata->'currency',
            'amount_source', metadata->'amount_source',
            'amount', metadata->'amount',
            'must_check_policy_version', metadata->'must_check_policy_version'
        )),
        metadata = metadata - ARRAY[
            'customer_tier','days_since_purchase','security_flag','amount_minor',
            'currency','amount_source','amount','must_check_policy_version'
        ]::text[]
        WHERE facts = '{}'::jsonb
        """
    )
    op.execute(
        """
        UPDATE loopie.tickets
        SET facts = facts || jsonb_build_object(
            'amount_minor', ROUND((facts->>'amount')::numeric * 100)::bigint,
            'currency', COALESCE(facts->>'currency', 'USD'),
            'amount_source', 'explicit'
        )
        WHERE facts ? 'amount' AND NOT facts ? 'amount_minor'
        """
    )

    op.execute("DROP TRIGGER IF EXISTS run_manifests_immutable ON loopie.run_manifests")
    op.execute("ALTER TABLE loopie.run_manifests ADD COLUMN IF NOT EXISTS ticket_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE loopie.run_manifests ADD COLUMN IF NOT EXISTS ticket_content_hash TEXT NOT NULL DEFAULT 'legacy-unverified'")
    op.execute("ALTER TABLE loopie.run_manifests ADD COLUMN IF NOT EXISTS evaluation_snapshot JSONB")
    op.execute("ALTER TABLE loopie.run_manifests ADD COLUMN IF NOT EXISTS scorer_version TEXT NOT NULL DEFAULT 'v2'")
    op.execute(
        """
        UPDATE loopie.run_manifests AS manifest
        SET ticket_snapshot = jsonb_build_object(
            'id', ticket.id::text,
            'external_id', ticket.external_id,
            'version', manifest.ticket_version,
            'subject', ticket.subject,
            'body', ticket.body,
            'channel', ticket.channel,
            'customer_ref', ticket.customer_ref,
            'facts', ticket.facts,
            'metadata', ticket.metadata,
            'tags', to_jsonb(ticket.tags)
        )
        FROM loopie.tickets AS ticket
        WHERE ticket.id = manifest.ticket_id AND manifest.ticket_snapshot = '{}'::jsonb
        """
    )
    op.execute(
        """
        UPDATE loopie.run_manifests AS manifest
        SET evaluation_snapshot = to_jsonb(golden)
        FROM loopie.golden_annotations AS golden
        WHERE golden.ticket_id = manifest.ticket_id
          AND manifest.evaluation_snapshot IS NULL
          AND EXISTS (
              SELECT 1 FROM loopie.runs AS run
              WHERE run.manifest_id = manifest.id AND run.kind = 'golden'
          )
        """
    )
    op.execute(
        """
        CREATE TRIGGER run_manifests_immutable
        BEFORE UPDATE OR DELETE ON loopie.run_manifests
        FOR EACH ROW EXECUTE FUNCTION loopie.reject_run_manifest_mutation()
        """
    )

    op.execute("ALTER TABLE loopie.audit_events ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES loopie.runs(id)")
    op.execute("CREATE INDEX IF NOT EXISTS audit_events_run_idx ON loopie.audit_events(run_id)")
    op.execute("ALTER TABLE loopie.cost_ledger ADD COLUMN IF NOT EXISTS operation TEXT")
    op.execute("ALTER TABLE loopie.cost_ledger ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE loopie.cost_ledger ADD COLUMN IF NOT EXISTS event_key TEXT")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS cost_ledger_event_key_idx ON loopie.cost_ledger(event_key) WHERE event_key IS NOT NULL")

    op.execute("ALTER TABLE loopie.runs ADD COLUMN IF NOT EXISTS weave_call_id TEXT")
    op.execute("ALTER TABLE loopie.runs ADD COLUMN IF NOT EXISTS weave_trace_id TEXT")
    op.execute("ALTER TABLE loopie.runs ADD COLUMN IF NOT EXISTS weave_url TEXT")
    op.execute("ALTER TABLE loopie.runs ADD COLUMN IF NOT EXISTS evidence_status TEXT NOT NULL DEFAULT 'incomplete'")
    op.execute("ALTER TABLE loopie.runs DROP CONSTRAINT IF EXISTS runs_evidence_status_check")
    op.execute("ALTER TABLE loopie.runs ADD CONSTRAINT runs_evidence_status_check CHECK (evidence_status IN ('complete','incomplete'))")

    taxonomy_json = json.dumps(ACTION_TAXONOMY, separators=(",", ":")).replace("'", "''")
    op.execute(
        f"UPDATE loopie.projects SET action_taxonomy = '{taxonomy_json}'::jsonb WHERE id = '{DEFAULT_PROJECT_ID}'::uuid"
    )
    op.execute(
        """
        UPDATE loopie.golden_annotations
        SET expected_action = 'escalate_security'
        WHERE expected_action = 'block_refund_tool'
        """
    )
    outbox_id = str(uuid.uuid5(uuid.UUID(DEFAULT_PROJECT_ID), "taxonomy:v2:outbox"))
    op.execute(
        f"""
        WITH next_version AS (
            SELECT COALESCE(MAX(version), 0) + 1 AS version
            FROM loopie.artifact_versions
            WHERE project_id = '{DEFAULT_PROJECT_ID}'::uuid
              AND artifact_key = 'config:action_taxonomy'
        ), seeded AS (
            INSERT INTO loopie.artifact_versions
                (project_id, artifact_key, version, value, source_case, status)
            SELECT '{DEFAULT_PROJECT_ID}'::uuid, 'config:action_taxonomy', version,
                   '{taxonomy_json}'::jsonb, 'migration:20260712_0003', 'active'
            FROM next_version
            RETURNING project_id, artifact_key, version, value
        )
        INSERT INTO loopie.artifact_outbox
            (id, project_id, correction_id, artifact_key, version, value)
        SELECT '{outbox_id}'::uuid, project_id, NULL, artifact_key, version, value
        FROM seeded
        ON CONFLICT (project_id, artifact_key, version) DO NOTHING
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260712_0003 is intentionally forward-only")
