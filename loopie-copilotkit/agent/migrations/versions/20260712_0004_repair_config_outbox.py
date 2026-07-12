"""Repair the v3 action-taxonomy artifact envelope.

Revision ID: 20260712_0004
Revises: 20260712_0003
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "20260712_0004"
down_revision: str | Sequence[str] | None = "20260712_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE loopie.artifact_versions
        SET value = jsonb_build_object(
            'key', 'action_taxonomy',
            'value', value
        )
        WHERE artifact_key = 'config:action_taxonomy'
          AND NOT (
              jsonb_typeof(value) = 'object'
              AND value ? 'key'
              AND value ? 'value'
          )
        """
    )
    op.execute(
        """
        UPDATE loopie.artifact_outbox
        SET value = jsonb_build_object(
            'key', 'action_taxonomy',
            'value', value
        )
        WHERE artifact_key = 'config:action_taxonomy'
          AND projected_at IS NULL
          AND NOT (
              jsonb_typeof(value) = 'object'
              AND value ? 'key'
              AND value ? 'value'
          )
        """
    )


def downgrade() -> None:
    # Keep the repaired envelope. Reintroducing the invalid row would break startup.
    pass
