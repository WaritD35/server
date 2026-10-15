"""Companion (context 6).

Writes the plant character's message for each run from the stored assessment
and advice. Owns the ``companion`` Postgres schema.

Public surface:

- :func:`generate_for_plant` — the pipeline's companion stage, called by
  ``orchestrator``.
- :func:`get_latest_message` — read interface for the other contexts; the
  message comes back as :class:`CompanionMessageRead`.
"""

from companion.interface import get_latest_message
from companion.schemas import CompanionMessageRead
from companion.service import generate_for_plant

__all__ = [
    "CompanionMessageRead",
    "generate_for_plant",
    "get_latest_message",
]
