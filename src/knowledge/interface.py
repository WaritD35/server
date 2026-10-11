"""Published in-process interface for other bounded contexts.

``assessment`` needs the probe definitions and ``advice`` needs the actions;
both call :func:`get_species_probes` instead of reaching into the ``knowledge``
schema. ``registry`` calls :func:`get_species` to check a plant's species code.
Return values are Pydantic models that serialise straight to JSON
(``bundle.model_dump(mode="json")``).
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import Species
from knowledge.schemas import ProbeRead, SpeciesProbesBundle, SpeciesRead
from knowledge.service import get_approved_probe_set, is_probe_set_stale

logger = logging.getLogger(__name__)


def get_species_probes(
    session: Session, species_code: str
) -> SpeciesProbesBundle | None:
    """The approved probes + actions for ``species_code``."""
    probe_set = get_approved_probe_set(session, species_code)
    if probe_set is None:
        return None
    return SpeciesProbesBundle(
        species_code=species_code,
        probe_set_id=probe_set.id,
        status=probe_set.status,
        is_stale=is_probe_set_stale(session, probe_set.id),
        generated_at=probe_set.generated_at,
        probes=[ProbeRead.model_validate(p) for p in probe_set.probes],
    )


def get_species(session: Session, species_code: str) -> SpeciesRead | None:
    """The catalogue entry for ``species_code``, or ``None`` if it is unknown."""
    species = session.get(Species, species_code)
    return None if species is None else SpeciesRead.model_validate(species)


def get_care_knowledge(
    session: Session,
    species_code: str,
    query_vector: list[float] | None = None,
    *,
    limit: int = 4,
    max_chars: int = 2500,
) -> str | None:
    """Botanical knowledge text for the species used by Care Advisor RAG."""
    from knowledge.models.chunk import KnowledgeChunk
    from knowledge.models.document import ResearchDocument

    try:
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.species_code == species_code)
        if query_vector is not None and KnowledgeChunk.embedding is not None:
            stmt = stmt.order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
        stmt = stmt.limit(limit)
        chunks = list(session.scalars(stmt).all())
        if chunks:
            parts = [f"### {c.topic}\n{c.content}" for c in chunks]
            return "\n\n".join(parts)[:max_chars]
    except Exception:
        logger.warning(
            "Vector chunk retrieval failed for species=%s, falling back to document",
            species_code,
            exc_info=True,
        )

    doc_stmt = select(ResearchDocument).where(
        ResearchDocument.species_code == species_code,
        ResearchDocument.status == "active",
    )
    doc = session.scalars(doc_stmt).first()
    if doc and doc.body:
        return doc.body[:max_chars]

    return None
