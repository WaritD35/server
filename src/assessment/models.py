"""SQLAlchemy models for the ``assessment`` schema."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from assessment.db import ASSESSMENT_SCHEMA, Base, utcnow


class Observation(Base):
    """Daily health snapshot and symptoms record."""

    __tablename__ = "observation"
    __table_args__ = (
        Index("ix_obs_plant_ts", "plant_id", "timestamp"),
        Index("ix_obs_run", "run_id"),
        {"schema": ASSESSMENT_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    health_status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    observations_json: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    consensus_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    image_refs_json: Mapped[list[Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    description: Mapped[str | None] = mapped_column(Text)
    companion_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TriggerResultRecord(Base):
    """The event engine's deterministic routing verdict for a pipeline run."""

    __tablename__ = "trigger_result"
    __table_args__ = (
        Index("uq_trigger_run", "run_id", unique=True),
        Index("ix_trigger_plant", "plant_id"),
        {"schema": ASSESSMENT_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Decision: 'CARE_ADVICE_REQUIRED' | 'NO_ACTION' | 'REQUEST_MORE_INFORMATION'
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    primary_symptom: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PlantMilestone(Base):
    """Major life events in a plant's historical lifecycle."""

    __tablename__ = "plant_milestone"
    __table_args__ = (
        Index("ix_milestone_plant_ts", "plant_id", "timestamp"),
        Index("ix_milestone_plant_type", "plant_id", "event_type"),
        {"schema": ASSESSMENT_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
