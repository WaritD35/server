"""Plant Registry (context 1).

Identity & physical anchoring: the persistent record of which physical plant is
which — who owns it, what species it is, and which device photographs it.
Owns the ``registry`` Postgres schema.

``plant.id`` is the identifier every other context keys its rows on, so plants
are archived rather than deleted.

Public surface:

- :data:`router` — the ``/registry`` endpoints (plant CRUD + device lookup),
  mounted by ``main_web``.
- :func:`list_plant_ids`, :func:`get_plant`, :func:`get_plant_by_device`,
  :func:`is_plant_owned_by` — in-process interface for the other contexts;
  plants come back as :class:`PlantRead`.
"""

from registry.api import router
from registry.interface import (
    get_plant,
    get_plant_by_device,
    is_plant_owned_by,
    list_plant_ids,
)
from registry.schemas import PlantRead

__all__ = [
    "PlantRead",
    "get_plant",
    "get_plant_by_device",
    "is_plant_owned_by",
    "list_plant_ids",
    "router",
]
