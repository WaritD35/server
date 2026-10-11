"""003 create vision schema

Revision ID: 003_vision_schema
Revises: 002_care_plan_action
Create Date: 2026-10-11 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '003_vision_schema'
down_revision: str | None = '002_care_plan_action'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "vision"'))
    op.create_table(
        'capture',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('plant_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=True),
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('raw_object_key', sa.Text(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('brightness', sa.Float(), nullable=True),
        sa.Column('blur_score', sa.Float(), nullable=True),
        sa.Column('quality_passed', sa.Boolean(), nullable=False),
        sa.Column('quality_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='vision',
        if_not_exists=True,
    )
    op.create_index(
        'ix_capture_plant_ts',
        'capture',
        ['plant_id', 'captured_at'],
        schema='vision',
        if_not_exists=True,
    )
    op.create_table(
        'processed_frame',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('capture_id', sa.Uuid(), nullable=False),
        sa.Column('plant_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=True),
        sa.Column('object_key', sa.Text(), nullable=False),
        sa.Column('crop_type', sa.Text(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('aligned_to_frame_id', sa.Uuid(), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['capture_id'],
            ['vision.capture.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='vision',
        if_not_exists=True,
    )
    op.create_index(
        'ix_processed_frame_plant_ts',
        'processed_frame',
        ['plant_id', 'processed_at'],
        schema='vision',
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_processed_frame_plant_ts',
        table_name='processed_frame',
        schema='vision',
        if_exists=True,
    )
    op.drop_table('processed_frame', schema='vision', if_exists=True)
    op.drop_index('ix_capture_plant_ts', table_name='capture', schema='vision', if_exists=True)
    op.drop_table('capture', schema='vision', if_exists=True)
    op.execute(sa.text('DROP SCHEMA IF EXISTS "vision" CASCADE'))
