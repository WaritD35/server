"""Species & Care Knowledge (context 3).

A person researches a species and writes it up as text (`research_document`); that
text is fed to an LLM which produces `probe`s (single-question probes) and their
`probe_action`s. Few-shot exemplar images live in the `exemplars` MinIO bucket.
Owns the `knowledge` Postgres schema.

Nothing is edited in place: a revised research text is a new row that archives
the old one, and approving another probe set archives the one it replaces, so a
species always has exactly one approved set (and archived sets can be swapped
back in).

Public surface:

- :data:`router` — the ``/knowledge`` endpoints (document authoring + probe
  inspection), mounted by ``main_web``.
- :func:`get_approved_checks` — in-process interface: ``species_code`` (and
  optionally the slugs needed) → just those approved checks, each with its
  approved basis and actions, in the order to use them. What ``assessment`` and
  ``advice`` read.
- :func:`get_species_probes` — the whole approved probe set, unordered.
- :func:`get_species` — in-process interface: ``species_code`` → catalogue
  entry (or ``None``), for ``registry``.
"""

from knowledge.api import router
from knowledge.interface import get_approved_checks, get_species, get_species_probes

__all__ = ["get_approved_checks", "get_species", "get_species_probes", "router"]
