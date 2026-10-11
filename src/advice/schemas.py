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
    action: str
    label: str
    type: ActionType = ActionType.OTHER


class CarePlanActionRead(BaseModel):
    id: uuid.UUID
    care_plan_pk: uuid.UUID
    care_plan_id: str
    action_id: str
    priority: int
    action: str
    label: str
    action_type: str
    created_at: datetime


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


class DiagnosisRead(BaseModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    run_id: uuid.UUID | None = None
    diagnosis: str
    created_at: datetime
