"""005 record the approved action behind each care plan step

Revision ID: 005_care_plan_action_source
Revises: 004_observation_probe_results
Create Date: 2026-10-12 21:00:00.000000

Care plans are now built only from approved ``knowledge`` probe actions. Each
step records which one it came from, its check, urgency and expected time to
effect. ``IF NOT EXISTS`` keeps this safe on a database whose table was already
built from the current models.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '005_care_plan_action_source'
down_revision: str | None = '004_observation_probe_results'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE advice.care_plan_action '
            'ADD COLUMN IF NOT EXISTS approved_action_id UUID, '
            'ADD COLUMN IF NOT EXISTS check_slug TEXT, '
            'ADD COLUMN IF NOT EXISTS urgency TEXT, '
            'ADD COLUMN IF NOT EXISTS expect_typical_hours INTEGER, '
            'ADD COLUMN IF NOT EXISTS expect_max_hours INTEGER'
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            'ALTER TABLE advice.care_plan_action '
            'DROP COLUMN IF EXISTS expect_max_hours, '
            'DROP COLUMN IF EXISTS expect_typical_hours, '
            'DROP COLUMN IF EXISTS urgency, '
            'DROP COLUMN IF EXISTS check_slug, '
            'DROP COLUMN IF EXISTS approved_action_id'
        )
    )
