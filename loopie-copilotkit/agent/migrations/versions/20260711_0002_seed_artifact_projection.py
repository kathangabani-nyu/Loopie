"""Seed authoritative artifact versions and projection outbox.

Revision ID: 20260711_0002
Revises: 20260711_0001
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = "20260711_0002"
down_revision: str | Sequence[str] | None = "20260711_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
SEED_DIR = Path(__file__).resolve().parents[1] / "seeds"
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


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).replace(":", r"\:")
    return _literal(encoded) + "::jsonb"


def _seed(artifact_key: str, value: object) -> None:
    outbox_id = uuid.uuid5(uuid.UUID(DEFAULT_PROJECT_ID), f"seed-outbox:{artifact_key}:1")
    op.execute(
        f"""
        WITH seeded AS (
            INSERT INTO loopie.artifact_versions
                (project_id, artifact_key, version, value, source_case, status)
            SELECT '{DEFAULT_PROJECT_ID}'::uuid, {_literal(artifact_key)}, 1,
                   {_json(value)}, 'migration:v2', 'active'
            WHERE NOT EXISTS (
                SELECT 1 FROM loopie.artifact_versions
                WHERE project_id = '{DEFAULT_PROJECT_ID}'::uuid
                  AND artifact_key = {_literal(artifact_key)}
            )
            RETURNING project_id, artifact_key, version, value
        )
        INSERT INTO loopie.artifact_outbox
            (id, project_id, correction_id, artifact_key, version, value)
        SELECT '{outbox_id}'::uuid, project_id, NULL, artifact_key, version, value
        FROM seeded
        ON CONFLICT (project_id, artifact_key, version) DO NOTHING
        """
    )


def upgrade() -> None:
    policies = json.loads((SEED_DIR / "v2_policy_rules.json").read_text(encoding="utf-8"))
    _seed("policy:rules", {"rules": policies})
    _seed("routing:rules", {"rules": []})
    _seed(
        "memory:policy:refund_window",
        {
            "key": "policy:refund_window",
            "value": "Refunds are allowed within 45 days.",
            "version": 1,
        },
    )
    _seed("config:max_transitions", {"key": "max_transitions", "value": 6})
    _seed("config:action_taxonomy", {"key": "action_taxonomy", "value": ACTION_TAXONOMY})


def downgrade() -> None:
    # Seed evidence is intentionally retained. Removing authoritative versions
    # during downgrade could make an already-produced run impossible to replay.
    pass
