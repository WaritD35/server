"""Persistence and read queries for the ``knowledge`` context.

No HTTP concerns and no LLM logic here — this module only stores and reads what
is already in the ``knowledge`` schema.

Nothing is ever overwritten or deleted: superseding a research document or
approving another probe set archives the row it replaces.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from knowledge.db import probe_set_freshness, utcnow
from knowledge.models import Probe, ProbeSet, ResearchDocument, Species
from knowledge.schemas import DocumentCreate, SpeciesCreate


class UnknownSpeciesError(Exception):
    """Raised when a document references a ``species_code`` that does not exist."""


class SpeciesAlreadyExistsError(Exception):
    """Raised when registering a ``species_code`` that is already taken."""


class ProbeSetTransitionError(Exception):
    """Raised for an illegal ``probe_set.status`` transition."""


# --------------------------------------------------------------------------- #
# species
# --------------------------------------------------------------------------- #


def create_species(session: Session, data: SpeciesCreate) -> Species:
    if session.get(Species, data.species_code) is not None:
        raise SpeciesAlreadyExistsError(data.species_code)
    species = Species(
        species_code=data.species_code,
        scientific_name=data.scientific_name,
        common_name=data.common_name,
    )
    session.add(species)
    session.commit()
    session.refresh(species)
    return species


def list_species(session: Session) -> Sequence[Species]:
    return session.scalars(
        select(Species).order_by(Species.species_code)
    ).all()


# --------------------------------------------------------------------------- #
# research_document (append-only)
# --------------------------------------------------------------------------- #


def get_active_document(
    session: Session, species_code: str
) -> ResearchDocument | None:
    """The species' current research text, or ``None`` before the first one."""
    return session.scalars(
        select(ResearchDocument).where(
            ResearchDocument.species_code == species_code,
            ResearchDocument.status == "active",
        )
    ).one_or_none()


def create_document(session: Session, data: DocumentCreate) -> ResearchDocument:
    """Insert a new document and archive the one it supersedes.

    Revising the research text means adding a row, never editing one: the probe
    sets generated from the previous text keep pointing at it (and start showing
    ``is_stale``).
    """
    if session.get(Species, data.species_code) is None:
        raise UnknownSpeciesError(data.species_code)
    previous = get_active_document(session, data.species_code)
    if previous is not None:
        previous.status = "archived"
        previous.archived_at = utcnow()
        # Free the "one active document per species" index before inserting.
        session.flush()
    doc = ResearchDocument(
        species_code=data.species_code,
        title=data.title,
        body=data.body,
        author=data.author,
        source_url=data.source_url,
        source_note=data.source_note,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def get_document(session: Session, doc_id: uuid.UUID) -> ResearchDocument | None:
    return session.get(ResearchDocument, doc_id)


def list_documents(
    session: Session, *, species_code: str | None = None, status: str | None = None
) -> Sequence[ResearchDocument]:
    stmt = select(ResearchDocument).order_by(ResearchDocument.created_at.desc())
    if species_code is not None:
        stmt = stmt.where(ResearchDocument.species_code == species_code)
    if status is not None:
        stmt = stmt.where(ResearchDocument.status == status)
    return session.scalars(stmt).all()


def list_unconverted_documents(
    session: Session, *, species_code: str | None = None, limit: int | None = None
) -> Sequence[ResearchDocument]:
    """Active documents that no probe set has ever been generated from.

    The backlog :func:`knowledge.generation.generate_pending` works through.
    "Converted" means *any* probe set points at the document — draft, approved
    or archived. A rejected draft therefore does **not** put its document back
    in the queue: a scheduled job that regenerated after every rejection would
    do so forever, and a person already decided that prompt cannot handle this
    text. Regenerating is a deliberate act (generate for that document again),
    not something the backlog does on its own.

    Documents are append-only, so a revised text is a *new* row with no set of
    its own and appears here without any hash comparison. Oldest first, so a
    limited run works through the backlog in the order it accumulated.
    """
    already_generated = select(ProbeSet.research_document_id)
    stmt = (
        select(ResearchDocument)
        .where(
            ResearchDocument.status == "active",
            ResearchDocument.id.not_in(already_generated),
        )
        .order_by(ResearchDocument.created_at)
    )
    if species_code is not None:
        stmt = stmt.where(ResearchDocument.species_code == species_code)
    if limit is not None:
        stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


# --------------------------------------------------------------------------- #
# probe inspection
# --------------------------------------------------------------------------- #

_PROBE_LOADERS = (
    selectinload(ProbeSet.probes).selectinload(Probe.actions),
    selectinload(ProbeSet.probes).selectinload(Probe.exemplars),
)


def _staleness_map(session: Session) -> dict[uuid.UUID, bool]:
    rows = session.execute(
        select(probe_set_freshness.c.probe_set_id, probe_set_freshness.c.is_stale)
    )
    return {sid: bool(stale) for sid, stale in rows}


def _probe_counts(
    session: Session, set_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not set_ids:
        return {}
    rows = session.execute(
        select(Probe.probe_set_id, func.count())
        .where(Probe.probe_set_id.in_(set_ids))
        .group_by(Probe.probe_set_id)
    )
    return {sid: count for sid, count in rows}


def list_probe_sets(
    session: Session, species_code: str
) -> list[tuple[ProbeSet, bool, int]]:
    """All probe sets for a species, newest first, with staleness + probe count."""
    sets = list(
        session.scalars(
            select(ProbeSet)
            .where(ProbeSet.species_code == species_code)
            .order_by(ProbeSet.generated_at.desc())
        ).all()
    )
    stale = _staleness_map(session)
    counts = _probe_counts(session, [s.id for s in sets])
    return [(s, stale.get(s.id, False), counts.get(s.id, 0)) for s in sets]


def get_probe_set(session: Session, set_id: uuid.UUID) -> ProbeSet | None:
    return session.scalars(
        select(ProbeSet).where(ProbeSet.id == set_id).options(*_PROBE_LOADERS)
    ).one_or_none()


def get_approved_probe_set(
    session: Session, species_code: str
) -> ProbeSet | None:
    """The species' single approved probe set, or ``None`` if it has none."""
    return session.scalars(
        select(ProbeSet)
        .where(
            ProbeSet.species_code == species_code,
            ProbeSet.status == "approved",
        )
        .options(*_PROBE_LOADERS)
    ).one_or_none()


def is_probe_set_stale(session: Session, set_id: uuid.UUID) -> bool:
    return _staleness_map(session).get(set_id, False)


# --------------------------------------------------------------------------- #
# probe_set status transitions
# --------------------------------------------------------------------------- #


def approve_probe_set(
    session: Session, probe_set: ProbeSet, *, approved_by: str
) -> ProbeSet:
    """Make ``probe_set`` the species' approved set, archiving the incumbent.

    Works from ``draft`` (a fresh generation run) and from ``archived`` (swapping
    an older set back in). Approval is exclusive per species — a partial unique
    index backs this up in the database.
    """
    if probe_set.status == "approved":
        raise ProbeSetTransitionError("probe set is already approved")
    if probe_set.status not in ("draft", "archived"):
        raise ProbeSetTransitionError(
            f"cannot approve a {probe_set.status} probe set"
        )
    if not probe_set.probes:
        raise ProbeSetTransitionError("cannot approve a probe set with no probes")

    now = utcnow()
    incumbent = get_approved_probe_set(session, probe_set.species_code)
    if incumbent is not None:
        incumbent.status = "archived"
        incumbent.archived_at = now
        # Free the "one approved set per species" index before promoting.
        session.flush()

    probe_set.status = "approved"
    probe_set.approved_by = approved_by
    probe_set.approved_at = now
    probe_set.archived_at = None
    # Approving a set that had been rejected overrides that verdict; leaving the
    # rejection stamped on the row would say two things at once.
    probe_set.rejected_by = None
    probe_set.rejected_at = None
    session.commit()
    session.refresh(probe_set)
    return probe_set


def reject_probe_set(
    session: Session, probe_set: ProbeSet, *, rejected_by: str, comment: str
) -> ProbeSet:
    """Record a reviewer turning down a draft, with their reason.

    Only ``draft`` sets can be rejected — an approved set is retired with
    :func:`archive_probe_set`, which is a different act. The row is kept
    (nothing here is deleted): it stays as the record of what the prompt
    produced and why a person refused it, and ``comment`` is the part a prompt
    revision is actually written against.
    """
    if probe_set.status != "draft":
        raise ProbeSetTransitionError(
            f"cannot reject a {probe_set.status} probe set; only drafts"
        )
    probe_set.status = "archived"
    probe_set.rejected_by = rejected_by
    probe_set.rejected_at = utcnow()
    probe_set.archived_at = probe_set.rejected_at
    probe_set.note = comment
    session.commit()
    session.refresh(probe_set)
    return probe_set


def archive_probe_set(session: Session, probe_set: ProbeSet) -> ProbeSet:
    """Retire the approved set, leaving the species with none until re-approval."""
    if probe_set.status != "approved":
        raise ProbeSetTransitionError(
            f"cannot archive a {probe_set.status} probe set"
        )
    probe_set.status = "archived"
    probe_set.archived_at = utcnow()
    session.commit()
    session.refresh(probe_set)
    return probe_set
