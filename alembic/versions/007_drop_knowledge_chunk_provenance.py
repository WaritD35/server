"""007 drop the passage-retrieval columns from knowledge.knowledge_chunks

Revision ID: 007_drop_chunk_provenance
Revises: 006_knowledge_chunk_provenance
Create Date: 2026-10-13 22:00:00.000000

Passage retrieval (006) is withdrawn: the approved probe set is already
structured, so assessment and advice read it through ``knowledge``'s interface
instead of searching the research document. This undoes 006 rather than
deleting it, so a database that already ran 006 stays on a valid history.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007_drop_chunk_provenance"
down_revision: str | None = "006_knowledge_chunk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_chunks() -> bool:
    # 001 builds tables from the current models, which no longer include
    # knowledge_chunks (dropped in 008), so a fresh database never has it.
    return sa.inspect(op.get_bind()).has_table("knowledge_chunks", schema="knowledge")


def upgrade() -> None:
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


def downgrade() -> None:
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
