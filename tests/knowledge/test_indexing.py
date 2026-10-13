"""Chunking and indexing the approved research document."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from knowledge.chunking import chunk_document
from knowledge.indexing import index_approved_probe_set
from knowledge.models import KnowledgeChunk, ProbeSet
from mlops.embeddings import embedding_model


def _vector() -> list[float]:
    return [0.01] * 1536


def test_chunk_document_uses_headings_and_drops_empty_slices() -> None:
    body = "# Water\n\nKeep the soil moist.\n\n\n# Light\n\nBright indirect light.\n"
    passages = chunk_document(body)
    assert [(p.topic, p.content) for p in passages] == [
        ("Water", "Keep the soil moist."),
        ("Light", "Bright indirect light."),
    ]


def test_long_block_is_split_near_the_target_with_overlap() -> None:
    body = "word " * 400
    passages = chunk_document(body, target=1000, overlap=150)
    assert len(passages) > 1
    assert all(passage.topic == "general" for passage in passages)
    assert all(len(passage.content) <= 1000 for passage in passages)
    assert passages[1].content[:20] in passages[0].content


def test_index_replaces_chunks_while_the_set_is_approved(
    session_factory: sessionmaker[Session], seed_full
) -> None:
    with session_factory() as session:
        document, probe_set, _probe = seed_full(session)
        session.add(
            KnowledgeChunk(
                species_code="spath",
                topic="old",
                content="previous document",
                probe_set_id=probe_set.id,
                research_document_id=document.id,
                source_content_hash="stale",
            )
        )
        session.commit()
        probe_set_id = probe_set.id
        document_id = document.id

    with session_factory() as session:
        result = index_approved_probe_set(
            session, probe_set_id, embed=lambda texts: [_vector() for _ in texts]
        )
        assert result.indexed is True
        assert result.passage_count >= 1
        rows = list(session.scalars(select(KnowledgeChunk)).all())
        assert rows
        assert all(row.probe_set_id == probe_set_id for row in rows)
        assert all(row.research_document_id == document_id for row in rows)
        assert all(row.source_content_hash for row in rows)
        assert all(row.metadata_json["embedding_model"] == embedding_model() for row in rows)
        assert all(len(row.embedding) == 1536 for row in rows)
        assert "previous document" not in {row.content for row in rows}


def test_index_inserts_nothing_when_the_set_is_no_longer_approved(
    session_factory: sessionmaker[Session], seed_full
) -> None:
    with session_factory() as session:
        _document, probe_set, _probe = seed_full(session)
        probe_set_id = probe_set.id

        def _archive(_texts: list[str]) -> list[list[float]]:
            current = session.get(ProbeSet, probe_set_id)
            assert current is not None
            current.status = "archived"
            session.flush()
            return [_vector()]

        result = index_approved_probe_set(session, probe_set_id, embed=_archive)

        assert result.indexed is False
        assert result.error == "probe set is no longer approved"
        count = session.scalar(select(func.count()).select_from(KnowledgeChunk))
        assert count == 0


def test_bad_embedding_shape_inserts_nothing(
    session_factory: sessionmaker[Session], seed_full
) -> None:
    with session_factory() as session:
        _document, probe_set, _probe = seed_full(session)
        result = index_approved_probe_set(
            session, probe_set.id, embed=lambda texts: [[0.1, 0.2] for _ in texts]
        )
        assert result.indexed is False
        assert session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
        assert uuid.UUID(str(probe_set.id))
