"""Hybrid retrieval over the passages of the approved probe set.

Keyword rank and cosine rank are fused in Python. PostgreSQL runs each leg.
SQLite, used by the tests, has neither ``to_tsvector`` nor pgvector operators,
so a search there returns an empty string instead of failing the caller.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from knowledge.models import KnowledgeChunk
from knowledge.service import get_approved_probe_set
from mlops.embeddings import embed_texts

logger = logging.getLogger(__name__)

FUSION_K = 60
LEG_LIMIT = 20
PASSAGE_LIMIT = 4
MAX_CHARS = 1500


@dataclass(frozen=True)
class _Hit:
    id: str
    probe_set_id: uuid.UUID | None
    topic: str
    content: str


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = FUSION_K) -> list[str]:
    """Fuse ranked id lists. A higher score is a better rank."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + position)
    return sorted(scores, key=lambda item_id: scores[item_id], reverse=True)


def render_ranked_passages(
    hits: list[_Hit],
    keyword_ids: list[str],
    vector_ids: list[str],
    *,
    approved_probe_set_id: uuid.UUID,
    limit: int = PASSAGE_LIMIT,
    max_chars: int = MAX_CHARS,
) -> str:
    """Fuse the two legs, dropping any hit that is not from the approved set."""
    eligible = {hit.id: hit for hit in hits if hit.probe_set_id == approved_probe_set_id}
    order = reciprocal_rank_fusion(
        [
            [item_id for item_id in keyword_ids if item_id in eligible],
            [item_id for item_id in vector_ids if item_id in eligible],
        ]
    )
    parts: list[str] = []
    total = 0
    for item_id in order[:limit]:
        hit = eligible[item_id]
        piece = f"### {hit.topic}\n{hit.content}"
        if total and total + 2 + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece) + (2 if len(parts) > 1 else 0)
    return "\n\n".join(parts)[:max_chars]


def search_approved_passages(
    session: Session,
    species_code: str,
    query_text: str,
    *,
    embed=embed_texts,
) -> str:
    """Passages from the approved probe set for ``query_text``, or ``""``.

    Does not read :func:`knowledge.interface.get_care_knowledge`.
    """
    if not query_text.strip():
        return ""
    probe_set = get_approved_probe_set(session, species_code)
    if probe_set is None:
        return ""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return ""
    try:
        vector = embed([query_text])[0]
    except Exception:
        logger.warning(
            "query embedding failed for species %s; keyword leg only",
            species_code,
            exc_info=True,
        )
        vector = None

    keyword_ids = _keyword_ids(session, probe_set.id, query_text)
    vector_ids = _vector_ids(session, probe_set.id, vector) if vector is not None else []
    if not keyword_ids and not vector_ids:
        return ""
    hits = _load_hits(session, probe_set.id)
    return render_ranked_passages(hits, keyword_ids, vector_ids, approved_probe_set_id=probe_set.id)


def _keyword_ids(session: Session, probe_set_id: uuid.UUID, query_text: str) -> list[str]:
    try:
        rows = session.execute(
            text(
                "SELECT id::text AS id "
                "FROM knowledge.knowledge_chunks "
                "WHERE probe_set_id = :probe_set_id "
                "AND to_tsvector('english', content) @@ plainto_tsquery('english', :query) "
                "ORDER BY ts_rank(to_tsvector('english', content), "
                "plainto_tsquery('english', :query)) DESC "
                "LIMIT :limit"
            ),
            {"probe_set_id": probe_set_id, "query": query_text, "limit": LEG_LIMIT},
        ).all()
    except Exception:
        logger.warning("keyword passage search failed", exc_info=True)
        return []
    return [str(row.id) for row in rows]


def _vector_ids(
    session: Session, probe_set_id: uuid.UUID, vector: list[float]
) -> list[str]:
    try:
        rows = session.execute(
            text(
                "SELECT id::text AS id "
                "FROM knowledge.knowledge_chunks "
                "WHERE probe_set_id = :probe_set_id AND embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:vector AS vector) "
                "LIMIT :limit"
            ),
            {
                "probe_set_id": probe_set_id,
                "vector": "[" + ",".join(str(value) for value in vector) + "]",
                "limit": LEG_LIMIT,
            },
        ).all()
    except Exception:
        logger.warning("vector passage search failed", exc_info=True)
        return []
    return [str(row.id) for row in rows]


def _load_hits(session: Session, probe_set_id: uuid.UUID) -> list[_Hit]:
    rows = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.probe_set_id == probe_set_id)
    ).all()
    return [
        _Hit(id=str(row.id), probe_set_id=row.probe_set_id, topic=row.topic, content=row.content)
        for row in rows
    ]
