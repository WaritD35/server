"""Replace a species' passages with the approved research document.

``knowledge.service`` deletes passages inside the approval and archive
transactions and does not import this module. Embedding happens here, after
that commit, and a row is written only while the probe set is still approved.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from knowledge.chunking import chunk_document
from knowledge.models import KnowledgeChunk, ProbeSet, ResearchDocument
from mlops.embeddings import EMBEDDING_DIMENSIONS, embed_texts, embedding_model

logger = logging.getLogger(__name__)

Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class IndexResult:
    """What a passage index attempt did."""

    species_code: str
    probe_set_id: uuid.UUID | None
    research_document_id: uuid.UUID | None
    passage_count: int
    indexed: bool
    error: str | None


def delete_species_chunks(session: Session, species_code: str) -> None:
    """Remove every passage stored for ``species_code``. Does not commit."""
    session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.species_code == species_code))


def index_approved_probe_set(
    session: Session,
    probe_set_id: uuid.UUID,
    *,
    embed: Embedder | None = None,
) -> IndexResult:
    """Chunk and embed the document behind ``probe_set_id`` when it is approved.

    Embeddings are computed before the write. The write deletes and inserts
    only if that set is still the approved one, so a slower index for an older
    set cannot replace a newer approval.
    """
    embed_fn = embed if embed is not None else embed_texts
    probe_set = session.get(ProbeSet, probe_set_id)
    if probe_set is None:
        return IndexResult("", probe_set_id, None, 0, False, "probe set not found")
    document = session.get(ResearchDocument, probe_set.research_document_id)
    species_code = probe_set.species_code
    document_id = probe_set.research_document_id
    if probe_set.status != "approved":
        return IndexResult(
            species_code, probe_set_id, document_id, 0, False, "probe set is not approved"
        )
    if document is None or not document.body.strip():
        return IndexResult(
            species_code, probe_set_id, document_id, 0, False, "research document is empty"
        )

    passages = chunk_document(document.body)
    if not passages:
        return IndexResult(
            species_code, probe_set_id, document_id, 0, False, "document produced no passages"
        )
    try:
        vectors = embed_fn([passage.content for passage in passages])
    except Exception as exc:
        logger.warning(
            "passage embedding failed for probe set %s (%s)",
            probe_set_id,
            species_code,
            exc_info=True,
        )
        return IndexResult(species_code, probe_set_id, document_id, 0, False, str(exc))
    wrong_shape = len(vectors) != len(passages) or any(
        len(vector) != EMBEDDING_DIMENSIONS for vector in vectors
    )
    if wrong_shape:
        return IndexResult(
            species_code,
            probe_set_id,
            document_id,
            0,
            False,
            "embedding returned an unexpected shape",
        )

    session.refresh(probe_set)
    if probe_set.status != "approved":
        return IndexResult(
            species_code,
            probe_set_id,
            document_id,
            0,
            False,
            "probe set is no longer approved",
        )

    delete_species_chunks(session, species_code)
    for passage, vector in zip(passages, vectors, strict=True):
        session.add(
            KnowledgeChunk(
                species_code=species_code,
                topic=passage.topic,
                content=passage.content,
                metadata_json={"embedding_model": embedding_model()},
                research_document_id=document_id,
                probe_set_id=probe_set_id,
                source_content_hash=document.content_hash,
                embedding=vector,
            )
        )
    session.commit()
    return IndexResult(species_code, probe_set_id, document_id, len(passages), True, None)
