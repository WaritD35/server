"""SQLAlchemy model for knowledge chunks with pgvector embeddings."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from knowledge.db import KNOWLEDGE_SCHEMA, Base, JsonB, utcnow


class KnowledgeChunk(Base):
    """A passage of an approved research document, with its embedding.

    Canonical text stays on ``research_document``. These rows are retrieval
    infrastructure for the VLM probe and the advice assessment, and are replaced
    when a new probe set is approved.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunk_species", "species_code"),
        Index("ix_knowledge_chunk_probe_set", "probe_set_id"),
        {"schema": KNOWLEDGE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    species_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    #: The document this passage was cut from. Null only on rows written before
    #: provenance existed; the indexer always sets it.
    research_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(f"{KNOWLEDGE_SCHEMA}.research_document.id"),
        nullable=True,
    )
    #: The approved probe set that authorised this passage. Retrieval filters on it.
    probe_set_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(f"{KNOWLEDGE_SCHEMA}.probe_set.id"),
        nullable=True,
    )
    #: SHA-256 of the document body at index time.
    source_content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 1536-dimensional vector. OpenAI text-embedding-3-small is native;
    # shorter or longer Ollama vectors are fitted to this width.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
