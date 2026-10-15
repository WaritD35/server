"""008 drop tables and columns nothing reads

Revision ID: 008_drop_unused
Revises: 007_drop_chunk_provenance
Create Date: 2026-10-15 12:00:00.000000

- ``knowledge.knowledge_chunks``: free research text with embeddings. The
  pipeline reads only the approved, structured probe set, so nothing reads or
  writes it. With it goes the ``vector`` extension, its only user.
- ``advice.diagnosis``: a copy of ``care_plan.assessment`` that was written
  but never read.
- ``advice.care_plan.actions_json``: the pre-``care_plan_action`` copy of the
  actions, kept for the expand-contract move that is now finished.
- ``knowledge.probe_exemplar``: few-shot images for probes, dropped from the
  prototype; nothing wrote a row.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_drop_unused"
down_revision: str | None = "007_drop_chunk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS knowledge.knowledge_chunks"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
    op.execute(sa.text("DROP TABLE IF EXISTS advice.diagnosis"))
    op.execute(sa.text("ALTER TABLE advice.care_plan DROP COLUMN IF EXISTS actions_json"))
    op.execute(sa.text("DROP TABLE IF EXISTS knowledge.probe_exemplar"))


def downgrade() -> None:
    # Recreating knowledge_chunks needs the pgvector extension, so this only
    # works on a pgvector-enabled image (e.g. pgvector/pgvector:pg16).
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS knowledge.probe_exemplar ("
            "id UUID PRIMARY KEY, "
            "probe_id UUID NOT NULL REFERENCES knowledge.probe (id), "
            "role TEXT NOT NULL, "
            "storage_key TEXT NOT NULL UNIQUE, "
            "content_type TEXT, "
            "label JSONB, "
            "origin TEXT NOT NULL, "
            "license TEXT, "
            "caption TEXT, "
            "created_at TIMESTAMPTZ NOT NULL)"
        )
    )
    op.execute(sa.text("ALTER TABLE advice.care_plan ADD COLUMN IF NOT EXISTS actions_json JSONB"))
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS advice.diagnosis ("
            "id UUID PRIMARY KEY, "
            "plant_id UUID NOT NULL, "
            "run_id UUID, "
            "diagnosis TEXT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL)"
        )
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_diagnosis_plant_id ON advice.diagnosis (plant_id)")
    )
    op.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_diagnosis_run_id ON advice.diagnosis (run_id)")
    )
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS knowledge.knowledge_chunks ("
            "id UUID PRIMARY KEY, "
            "species_code TEXT NOT NULL, "
            "topic TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "metadata_json JSONB NOT NULL, "
            "embedding vector(1536), "
            "created_at TIMESTAMPTZ NOT NULL)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_species "
            "ON knowledge.knowledge_chunks (species_code)"
        )
    )
