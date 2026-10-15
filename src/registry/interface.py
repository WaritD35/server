"""Published in-process interface for other bounded contexts.

Every context that keys data on a plant (``capture``, ``assessment``,
``advice``, ``companion``, ``orchestrator``) resolves it here instead of reaching
into the ``registry`` schema. Return values are Pydantic models that serialise
straight to JSON (``plant.model_dump(mode="json")``).
"""

import uuid

from sqlalchemy.orm import Session

from registry import service
from registry.schemas import PlantRead


def list_plant_ids(
    session: Session,
    *,
    owner_id: uuid.UUID | None = None,
    species_code: str | None = None,
    include_archived: bool = False,
) -> list[uuid.UUID]:
    """Ids of the registered plants, oldest first.

    Live plants only unless ``include_archived``. ``owner_id`` / ``species_code``
    narrow the list (e.g. every live plant of one species, for a batch run).
    """
    return service.list_plant_ids(
        session,
        owner_id=owner_id,
        species_code=species_code,
        include_archived=include_archived,
    )


def get_plant(session: Session, plant_id: uuid.UUID) -> PlantRead | None:
    """One plant's identity, or ``None`` if the id was never registered.

    Archived plants are returned too (``archived_at`` is set): history in other
    contexts keeps pointing at them.
    """
    plant = service.get_plant(session, plant_id)
    return None if plant is None else PlantRead.model_validate(plant)


def get_plant_by_device(session: Session, device_id: str) -> PlantRead | None:
    """The live plant a physical device photographs, or ``None`` if unbound.

    What ``capture`` calls with ``CurrentDevice.physical_id`` when a frame
    arrives. A binding made by an earlier owner of the device resolves to
    ``None``, never to that owner's plant.
    """
    plant = service.resolve_device_plant(session, device_id)
    return None if plant is None else PlantRead.model_validate(plant)


def is_plant_owned_by(session: Session, plant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether ``user_id`` owns ``plant_id`` — for other contexts' access checks."""
    plant = service.get_plant(session, plant_id)
    return plant is not None and plant.owner_id == user_id
