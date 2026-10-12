"""SQLAlchemy models for the ``vision`` schema.

``plant_id`` and ``run_id`` point into ``registry`` and ``orchestrator`` by value
only; there are no cross-schema foreign keys.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from capture.db import VISION_SCHEMA, Base, utcnow


class Capture(Base):
    """One photo uploaded by a planter, stored whether or not it passed the quality gate.

    ``run_id`` stays ``NULL`` until the capture stage claims the photo.
    """

    __tablename__ = "capture"
    __table_args__ = (
        Index("ix_capture_plant_ts", "plant_id", "captured_at"),
        {"schema": VISION_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    device_id: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    brightness: Mapped[float | None] = mapped_column(Float)
    blur_score: Mapped[float | None] = mapped_column(Float)
    quality_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ProcessedFrame(Base):
    """A capture that passed quality, preprocessed and aligned to the previous frame."""

    __tablename__ = "processed_frame"
    __table_args__ = (
        Index("ix_processed_frame_plant_ts", "plant_id", "processed_at"),
        {"schema": VISION_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capture_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{VISION_SCHEMA}.capture.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    crop_type: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    aligned_to_frame_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
