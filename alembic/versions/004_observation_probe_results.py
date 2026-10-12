"""004 add probe results to assessment.observation

Revision ID: 004_observation_probe_results
Revises: 003_vision_schema
Create Date: 2026-10-12 18:00:00.000000

The approved ``knowledge`` probes are now asked during assessment; each
observation records which probe set was used and every check's answer.

``001`` builds tables from the current models, so on a fresh database these
columns already exist — hence ``IF NOT EXISTS``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '004_observation_probe_results'
down_revision: str | None = '003_vision_schema'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE assessment.observation '
            'ADD COLUMN IF NOT EXISTS probe_set_id UUID, '
            'ADD COLUMN IF NOT EXISTS probe_results_json JSONB'
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE assessment.observation '
            'DROP COLUMN IF EXISTS probe_results_json, '
            'DROP COLUMN IF EXISTS probe_set_id'
        )
    )
