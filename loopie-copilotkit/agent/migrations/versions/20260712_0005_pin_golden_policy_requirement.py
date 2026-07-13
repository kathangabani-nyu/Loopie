"""Pin the Golden security case to its required approved policy artifact.

Revision ID: 20260712_0005
Revises: 20260712_0004
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "20260712_0005"
down_revision: str | Sequence[str] | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE loopie.golden_annotations AS golden
        SET expected_metadata = golden.expected_metadata ||
            '{"required_policy_rule_ids":["security_flag_requires_escalation"]}'::jsonb,
            annotated_by = 'migration:v3'
        FROM loopie.tickets AS ticket
        WHERE golden.ticket_id = ticket.id
          AND ticket.external_id = 'security_001'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE loopie.golden_annotations AS golden
        SET expected_metadata = golden.expected_metadata - 'required_policy_rule_ids'
        FROM loopie.tickets AS ticket
        WHERE golden.ticket_id = ticket.id
          AND ticket.external_id = 'security_001'
        """
    )
