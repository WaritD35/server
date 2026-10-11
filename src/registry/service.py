"""Persistence and read queries for the ``registry`` context.

No HTTP concerns here. References into other contexts (the owner and the
paired device in ``core``, the species in ``knowledge``) are checked through
their published interfaces — the ``registry`` schema holds no foreign key to
any of them.

Plants are never deleted: :func:`archive_plant` stamps ``archived_at``, because
every other context keys its history on ``plant.id``.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.devices import get_device_by_physical_id
from core.users.service import get_user_by_id
from knowledge.interface import get_species
from registry.db import utcnow
from registry.models import Plant
from registry.schemas import PlantCreate, PlantUpdate


class UnknownSpeciesError(Exception):
    """Raised when ``species_code`` is not in the ``knowledge`` catalogue."""


class UnknownOwnerError(Exception):
    """Raised when ``owner_id`` is not a registered user."""


class UnknownDeviceError(Exception):
    """Raised when ``device_id`` is not a device paired to the plant's owner."""


class DeviceAlreadyBoundError(Exception):
    """Raised when ``device_id`` already belongs to another live plant."""


class PlantArchivedError(Exception):
    """Raised when changing a plant that has been archived."""


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def _check_species(session: Session, species_code: str | None) -> None:
    if species_code is not None and get_species(session, species_code) is None:
        raise UnknownSpeciesError(species_code)


def _check_device(
    session: Session,
    device_id: str | None,
    *,
    owner_id: uuid.UUID,
    except_plant: uuid.UUID | None = None,
) -> None:
    """``device_id`` must be paired to ``owner_id`` and not bound to another plant.

    A binding left behind by a *previous* owner of the device (it was revoked
    and paired to someone else) is stale: it is cleared rather than reported as
    a conflict, so the new owner can bind the device.
    """
    if device_id is None:
        return
    device = get_device_by_physical_id(session, device_id)
    if device is None or device.owner_id != owner_id:
        raise UnknownDeviceError(device_id)
    holder = get_live_plant_by_device(session, device_id)
    if holder is None or holder.id == except_plant:
        return
    if holder.owner_id != device.owner_id:
        holder.device_id = None
        session.flush()
        return
    raise DeviceAlreadyBoundError(device_id)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def get_plant(session: Session, plant_id: uuid.UUID) -> Plant | None:
    """One plant, live or archived."""
    return session.get(Plant, plant_id)


def get_live_plant_by_device(session: Session, device_id: str) -> Plant | None:
    """The live plant row carrying ``device_id``, if any (at most one).

    Raw lookup — it does not check the device still belongs to the plant's
    owner. Resolve a device to its plant with :func:`resolve_device_plant`.
    """
    return session.scalars(
        select(Plant).where(Plant.device_id == device_id, Plant.archived_at.is_(None))
    ).one_or_none()


def resolve_device_plant(session: Session, device_id: str) -> Plant | None:
    """The live plant ``device_id`` photographs, as far as its current owner is concerned.

    ``None`` when the device is unbound, or when the binding was made by an
    earlier owner of the device (frames from the new owner's device must never
    land on the old owner's plant).
    """
    plant = get_live_plant_by_device(session, device_id)
    if plant is None:
        return None
    device = get_device_by_physical_id(session, device_id)
    if device is None or device.owner_id != plant.owner_id:
        return None
    return plant


def list_plants(
    session: Session,
    *,
    owner_id: uuid.UUID | None = None,
    species_code: str | None = None,
    include_archived: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> Sequence[Plant]:
    """Plants, oldest first; live ones only unless ``include_archived``."""
    stmt = select(Plant).order_by(Plant.created_at, Plant.id)
    if owner_id is not None:
        stmt = stmt.where(Plant.owner_id == owner_id)
    if species_code is not None:
        stmt = stmt.where(Plant.species_code == species_code)
    if not include_archived:
        stmt = stmt.where(Plant.archived_at.is_(None))
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


def list_plant_ids(
    session: Session,
    *,
    owner_id: uuid.UUID | None = None,
    species_code: str | None = None,
    include_archived: bool = False,
) -> list[uuid.UUID]:
    """Same filter as :func:`list_plants`, ids only."""
    stmt = select(Plant.id).order_by(Plant.created_at, Plant.id)
    if owner_id is not None:
        stmt = stmt.where(Plant.owner_id == owner_id)
    if species_code is not None:
        stmt = stmt.where(Plant.species_code == species_code)
    if not include_archived:
        stmt = stmt.where(Plant.archived_at.is_(None))
    return list(session.scalars(stmt).all())


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #


def create_plant(session: Session, data: PlantCreate, *, owner_id: uuid.UUID) -> Plant:
    if get_user_by_id(session, owner_id) is None:
        raise UnknownOwnerError(str(owner_id))
    _check_species(session, data.species_code)
    _check_device(session, data.device_id, owner_id=owner_id)

    plant = Plant(
        owner_id=owner_id,
        name=data.name,
        species_code=data.species_code,
        species_confirmed_at=(
            utcnow() if data.species_confirmed and data.species_code else None
        ),
        device_id=data.device_id,
        note=data.note,
    )
    session.add(plant)
    session.commit()
    session.refresh(plant)
    return plant


def update_plant(session: Session, plant: Plant, data: PlantUpdate) -> Plant:
    """Apply the fields present in ``data``.

    Changing ``species_code`` drops the owner's confirmation unless the same
    request confirms the new one.
    """
    if plant.archived_at is not None:
        raise PlantArchivedError(str(plant.id))
    changes: dict[str, Any] = data.model_dump(exclude_unset=True)
    confirmed = changes.pop("species_confirmed", None)

    if "species_code" in changes:
        _check_species(session, changes["species_code"])
        if changes["species_code"] != plant.species_code:
            plant.species_confirmed_at = None
    if "device_id" in changes:
        _check_device(
            session, changes["device_id"], owner_id=plant.owner_id, except_plant=plant.id
        )
    if "name" in changes and changes["name"] is None:
        del changes["name"]  # a plant always has a name

    for field, value in changes.items():
        setattr(plant, field, value)

    if confirmed is True and plant.species_code is not None:
        plant.species_confirmed_at = plant.species_confirmed_at or utcnow()
    elif confirmed is False:
        plant.species_confirmed_at = None

    session.commit()
    session.refresh(plant)
    return plant


def archive_plant(session: Session, plant: Plant) -> Plant:
    """Retire the plant; its id stays resolvable and its device is freed."""
    if plant.archived_at is not None:
        raise PlantArchivedError(str(plant.id))
    plant.archived_at = utcnow()
    session.commit()
    session.refresh(plant)
    return plant
