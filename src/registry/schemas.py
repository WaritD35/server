"""Request / response models for the ``registry`` HTTP API and the published
in-process interface.

:class:`PlantRead` is both the HTTP response and the payload other contexts
receive from :mod:`registry.interface`.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    species_code: str | None = Field(default=None, min_length=1, max_length=64)
    #: The owner vouches for ``species_code`` (e.g. picked it themselves).
    species_confirmed: bool = False
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=1000)
    #: Admin only: register the plant on someone else's behalf. Owners always
    #: register for themselves.
    owner_id: uuid.UUID | None = None


class PlantUpdate(BaseModel):
    """Partial update: only the fields present in the request are applied.

    Sending ``null`` for ``species_code`` / ``device_id`` / ``note`` clears it
    (``device_id: null`` unbinds the device).
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    species_code: str | None = Field(default=None, min_length=1, max_length=64)
    species_confirmed: bool | None = None
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


class PlantRead(BaseModel):
    """One plant's identity — the published payload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    species_code: str | None
    species_confirmed_at: datetime | None
    device_id: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    level: int = 1
    xp_ratio: float = 0.0
    archived_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.archived_at is None
