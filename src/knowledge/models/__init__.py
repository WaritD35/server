"""ORM models for the ``knowledge`` schema.

Importing this package registers every table on
:data:`knowledge.db.Base.metadata`.

Chain of ownership::

    species
      └── research_document      human-written research text (the LLM input)
            └── probe_set        one LLM generation run (+ human approval)
                  └── probe      one single-question probe
                        ├── probe_action     what to do when it fires
                        └── probe_exemplar   few-shot image (bytes in MinIO)

``research_document`` and ``probe_set`` are both append-only with an
``active`` / ``approved`` singleton per species: superseding one archives the
row it replaces instead of overwriting or deleting it.
"""

from knowledge.models.action import ProbeAction
from knowledge.models.chunk import KnowledgeChunk
from knowledge.models.document import ResearchDocument
from knowledge.models.exemplar import ProbeExemplar
from knowledge.models.probe import Probe, ProbeSet
from knowledge.models.species import Species

__all__ = [
    "Probe",
    "ProbeAction",
    "ProbeExemplar",
    "ProbeSet",
    "ResearchDocument",
    "KnowledgeChunk",
    "Species",
]
