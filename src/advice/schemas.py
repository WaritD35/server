"""Pydantic schemas for the ``advice`` bounded context."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    WATER = "water"
    MOVE = "move"
    INSPECT = "inspect"
    OTHER = "other"


class CareAction(BaseModel):
    id: str
    priority: int = 1
    #: The approved instruction, verbatim.
    action: str
    label: str
    type: ActionType = ActionType.OTHER
    #: The approved ``knowledge`` action it came from (``None``: generic step of
    #: a plant with no approved actions).
    approved_action_id: uuid.UUID | None = None
    check_slug: str | None = None
    urgency: str | None = None
    expect_typical_hours: int | None = None
    expect_max_hours: int | None = None


class CarePlanRead(BaseModel):
    id: uuid.UUID
    care_plan_id: str
    plant_id: uuid.UUID
    run_id: uuid.UUID
    status_label: str
    assessment: str
    confidence: float
    actions: list[CareAction] = Field(default_factory=list)
    created_at: datetime

