"""006 record which approved document a knowledge chunk came from

Revision ID: 006_knowledge_chunk_provenance
Revises: 005_care_plan_action_source
Create Date: 2026-10-13 19:30:00.000000

Advice retrieves passages from the research document behind the approved probe
set. These columns are how a chunk is tied to that document and set. The GIN
index serves the keyword leg of hybrid search. ``IF NOT EXISTS`` keeps this
safe on a database whose table was already built from the current models.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_knowledge_chunk_provenance"
down_revision: str | None = "005_care_plan_action_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_chunks() -> bool:
    # 001 builds tables from the current models, which no longer include
    # knowledge_chunks (dropped in 008), so a fresh database never has it.
    return sa.inspect(op.get_bind()).has_table("knowledge_chunks", schema="knowledge")


def upgrade() -> None:
    if not _has_chunks():
        return
    op.execute(
        sa.text(
            "ALTER TABLE knowledge.knowledge_chunks "
            "ADD COLUMN IF NOT EXISTS research_document_id UUID "
            "REFERENCES knowledge.research_document (id), "
            "ADD COLUMN IF NOT EXISTS probe_set_id UUID "
            "REFERENCES knowledge.probe_set (id), "
            "ADD COLUMN IF NOT EXISTS source_content_hash TEXT"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_probe_set "
            "ON knowledge.knowledge_chunks (probe_set_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_content_fts "
            "ON knowledge.knowledge_chunks "
            "USING GIN (to_tsvector('english', content))"
        )
    )


def downgrade() -> None:
    if not _has_chunks():
        return
    op.execute(sa.text("DROP INDEX IF EXISTS knowledge.ix_knowledge_chunks_content_fts"))
    op.execute(sa.text("DROP INDEX IF EXISTS knowledge.ix_knowledge_chunk_probe_set"))
    op.execute(
        sa.text(
            "ALTER TABLE knowledge.knowledge_chunks "
            "DROP COLUMN IF EXISTS source_content_hash, "
            "DROP COLUMN IF EXISTS probe_set_id, "
            "DROP COLUMN IF EXISTS research_document_id"
        )
    )
