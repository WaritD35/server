"""``plant`` — one physical plant, the identity every other context refers to.

``plant.id`` is the identifier the rest of the system keys its own rows on
(capture batches, probe runs, advice, companion state). It therefore outlives
the plant: removing a plant **archives** the row (``archived_at``) instead of
deleting it, so history elsewhere never points at nothing.

Two columns reference other contexts. Neither is a database foreign key — a
context never constrains against another context's schema — so both are checked
by the service layer through the owning context's published interface:

- ``owner_id``      → ``auth.users.id`` (``core``)
- ``species_code``  → ``knowledge.species.species_code`` (``knowledge``)

``device_id`` is the physical identifier the planter reports when it uploads a
frame (serial / MAC / provisioning id). At most one *live* plant per device is
enforced by a partial unique index; an archived plant keeps its ``device_id`` as
a record of where its frames came from, and frees the device for a new plant.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from registry.db import REGISTRY_SCHEMA, Base, utcnow

#: One live plant per device; archived rows and unbound plants are unconstrained.
_LIVE_BOUND_ONLY = text("device_id IS NOT NULL AND archived_at IS NULL")


class Plant(Base):
    __tablename__ = "plant"
    __table_args__ = (
        Index(
            "uq_plant_live_device",
            "device_id",
            unique=True,
            postgresql_where=_LIVE_BOUND_ONLY,
            sqlite_where=_LIVE_BOUND_ONLY,
        ),
        {"schema": REGISTRY_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # ``auth.users.id`` — who the plant belongs to.
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # The owner's name for the plant ("Pothos in the kitchen").
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # ``knowledge.species.species_code``; ``None`` until the species is resolved.
    species_code: Mapped[str | None] = mapped_column(Text, index=True)
    # Set when the owner confirms ``species_code``; cleared if the species changes.
    species_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Physical id of the planter / camera that photographs this plant.
    device_id: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    level: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default=text("1")
    )
    xp_ratio: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, server_default=text("0.0")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Plant {self.name!r} {self.id}>"
