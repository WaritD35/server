"""``/registry`` router — plant registration and lookup (CRUD).

Owners see and change only their own plants; a superuser sees every plant and
can register one on someone else's behalf. A paired edge device (``CurrentDevice``)
can read the plant it photographs from ``/registry/devices/me/plant``.
Another owner's plant answers 404, not 403, so ids cannot be probed for
existence.

``DELETE`` archives rather than deletes: every other context keys its history on
``plant.id``. Mounted by ``main_web``.
"""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from core.db import get_session
from core.devices import CurrentDevice
from core.users import CurrentUser, User
from registry import service
from registry.models import Plant
from registry.schemas import PlantCreate, PlantRead, PlantUpdate

router = APIRouter(prefix="/registry", tags=["registry"])

SessionDep = Annotated[Session, Depends(get_session)]


def _plant_or_404(session: Session, plant_id: uuid.UUID, user: User) -> Plant:
    plant = service.get_plant(session, plant_id)
    if plant is None or not (user.is_superuser or plant.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    return plant


#: Service errors a write can raise → the status code and detail they answer with.
_ERROR_RESPONSES: dict[type[Exception], tuple[int, str]] = {
    service.UnknownSpeciesError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown species_code"),
    service.UnknownOwnerError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown owner_id"),
    service.UnknownDeviceError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "device_id is not a device paired to the plant's owner",
    ),
    service.DeviceAlreadyBoundError: (
        status.HTTP_409_CONFLICT,
        "device_id is already bound to another plant",
    ),
    service.PlantArchivedError: (status.HTTP_409_CONFLICT, "Plant is archived"),
}
_WRITE_ERRORS = tuple(_ERROR_RESPONSES)


def _write_errors(exc: Exception) -> HTTPException:
    return HTTPException(*_ERROR_RESPONSES[type(exc)])


@router.post("/plants", response_model=PlantRead, status_code=status.HTTP_201_CREATED)
def create_plant(payload: PlantCreate, session: SessionDep, user: CurrentUser) -> Plant:
    if payload.owner_id is not None and payload.owner_id != user.id and not user.is_superuser:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only an admin can register a plant for another user"
        )
    try:
        return service.create_plant(session, payload, owner_id=payload.owner_id or user.id)
    except _WRITE_ERRORS as exc:
        raise _write_errors(exc) from None


@router.get(
    "/plants",
    response_model=list[PlantRead],
    summary="List plants — the caller's own, or anyone's for an admin",
)
def list_plants(
    session: SessionDep,
    user: CurrentUser,
    owner_id: uuid.UUID | None = None,
    species_code: str | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Plant]:
    if not user.is_superuser:
        owner_id = user.id
    return service.list_plants(
        session,
        owner_id=owner_id,
        species_code=species_code,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.get("/plants/{plant_id}", response_model=PlantRead)
def get_plant(plant_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Plant:
    return _plant_or_404(session, plant_id, user)


@router.patch("/plants/{plant_id}", response_model=PlantRead)
def update_plant(
    plant_id: uuid.UUID, payload: PlantUpdate, session: SessionDep, user: CurrentUser
) -> Plant:
    plant = _plant_or_404(session, plant_id, user)
    try:
        return service.update_plant(session, plant, payload)
    except _WRITE_ERRORS as exc:
        raise _write_errors(exc) from None


@router.delete(
    "/plants/{plant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a plant (its id stays resolvable; its device is freed)",
)
def archive_plant(plant_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Response:
    plant = _plant_or_404(session, plant_id, user)
    try:
        service.archive_plant(session, plant)
    except _WRITE_ERRORS as exc:
        raise _write_errors(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Declared before `/devices/{device_id}/plant` so "me" is not taken for an id.
@router.get(
    "/devices/me/plant",
    response_model=PlantRead,
    summary="Device: the plant I photograph",
)
def get_my_plant(device: CurrentDevice, session: SessionDep) -> Plant:
    plant = service.resolve_device_plant(session, device.physical_id)
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plant bound to this device")
    return plant


@router.get(
    "/devices/{device_id}/plant",
    response_model=PlantRead,
    summary="The live plant a physical device is bound to",
)
def get_plant_by_device(device_id: str, session: SessionDep, user: CurrentUser) -> Plant:
    plant = service.resolve_device_plant(session, device_id)
    if plant is None or not (user.is_superuser or plant.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plant bound to this device")
    return plant
