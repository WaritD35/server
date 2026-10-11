"""SQLAlchemy models for the ``advice`` schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from advice.db import ADVICE_SCHEMA, Base, utcnow


class CarePlan(Base):
    """Structured botanical care plan prescribed by the Care Advisor agent."""

    __tablename__ = "care_plan"
    __table_args__ = (
        Index("uq_care_plan_run", "run_id", unique=True),
        Index("ix_care_plan_plant_id", "plant_id"),
        {"schema": ADVICE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    care_plan_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status_label: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # actions_json maintained for backwards-compatibility / expand-contract transition
    actions_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    actions: Mapped[list[CarePlanAction]] = relationship(
        "CarePlanAction",
        back_populates="care_plan",
        cascade="all, delete-orphan",
        order_by="CarePlanAction.priority",
        lazy="joined",
    )


class CarePlanAction(Base):
    """Normalized individual care action item prescribed in a CarePlan."""

    __tablename__ = "care_plan_action"
    __table_args__ = (
        Index("ix_care_plan_action_plan_id", "care_plan_id"),
        Index("ix_care_plan_action_plan_pk", "care_plan_pk"),
        {"schema": ADVICE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    care_plan_pk: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{ADVICE_SCHEMA}.care_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    care_plan_id: Mapped[str] = mapped_column(Text, nullable=False)
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    care_plan: Mapped[CarePlan] = relationship("CarePlan", back_populates="actions")


class Diagnosis(Base):
    """Analytical diagnosis record."""

    __tablename__ = "diagnosis"
    __table_args__ = (
        Index("ix_diagnosis_plant_id", "plant_id"),
        Index("ix_diagnosis_run_id", "run_id"),
        {"schema": ADVICE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
