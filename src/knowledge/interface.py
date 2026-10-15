"""Published in-process interface for other bounded contexts.

``assessment`` needs the approved checks and ``advice`` the checks that fired
with their actions; both call :func:`get_approved_checks`, which returns only
the approved, structured knowledge they need, in order, instead of either
reaching into the ``knowledge`` schema or searching research text.
``registry`` calls :func:`get_species` to check a plant's species code.
Return values are Pydantic models that serialise straight to JSON
(``bundle.model_dump(mode="json")``).
"""

import logging
from collections.abc import Sequence

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


def get_approved_checks(
    session: Session, species_code: str, slugs: Sequence[str] | None = None
) -> SpeciesProbesBundle | None:
    """Only the approved checks a caller needs, in the order to use them.

    The structured read of approved knowledge: no search, no research text.
    Each check carries its question, what it looks like, what it is not, its
    approved basis (``evidence_quote``) and its actions in ``ordering`` order.

    - ``slugs=None`` — every approved check, screening checks first, then by
      ``priority`` (1 = first). What ``assessment`` asks the VLM.
    - ``slugs`` given — just those checks, in the order given; a slug that is not
      in the approved set (e.g. the set changed since it was asked) is skipped.
      What ``advice`` needs for the checks that fired.

    ``None`` when the species has no approved probe set.
    """
    bundle = get_species_probes(session, species_code)
    if bundle is None:
        return None
    if slugs is None:
        probes = sorted(bundle.probes, key=lambda p: (not p.is_screening, p.priority))
    else:
        by_slug = {p.slug: p for p in bundle.probes}
        probes = [by_slug[slug] for slug in dict.fromkeys(slugs) if slug in by_slug]
    return bundle.model_copy(
        update={
            "probes": [
                p.model_copy(update={"actions": sorted(p.actions, key=lambda a: a.ordering)})
                for p in probes
            ]
        }
    )


def get_species(session: Session, species_code: str) -> SpeciesRead | None:
    """The catalogue entry for ``species_code``, or ``None`` if it is unknown."""
    species = session.get(Species, species_code)
    return None if species is None else SpeciesRead.model_validate(species)

