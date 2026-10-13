"""SQLAlchemy model for knowledge chunks with pgvector embeddings."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from knowledge.db import KNOWLEDGE_SCHEMA, Base, JsonB, utcnow


class KnowledgeChunk(Base):
    """Botanical knowledge text chunk with pgvector embedding for Care Advisor RAG."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunk_species", "species_code"),
        {"schema": KNOWLEDGE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    species_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False, default=dict)
    # 1536-dimensional vector for OpenAI text-embedding-3-small
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
