"""Pydantic schemas for the ``assessment`` bounded context."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    POSSIBLY_UNHEALTHY = "possibly_unhealthy"
    UNHEALTHY = "unhealthy"


class TriggerDecision(StrEnum):
    NO_ACTION = "NO_ACTION"
    CARE_ADVICE_REQUIRED = "CARE_ADVICE_REQUIRED"
    REQUEST_MORE_INFORMATION = "REQUEST_MORE_INFORMATION"


class MilestoneType(StrEnum):
    FIRST_SYMPTOM = "first_symptom"
    HEALTH_CRISIS = "health_crisis"
    SEVERE_EPISODE = "severe_episode"
    NEAR_DEATH = "near_death"
    FULL_RECOVERY = "full_recovery"


class Symptom(BaseModel):
    type: str
    severity: str
    description: str
    location: str | None = None


class TriggerResult(BaseModel):
    decision: TriggerDecision
    primary_symptom: str | None = None
    confidence: float = 1.0
    reasoning: str | None = None


class ObservationCreate(BaseModel):
    plant_id: uuid.UUID
    run_id: uuid.UUID
    timestamp: datetime
    health_status: HealthStatus
    confidence: float = 1.0
    observations: list[dict[str, Any]] = Field(default_factory=list)
    consensus: dict[str, Any] | None = None
    image_refs: list[str] | None = None
    description: str | None = None


class ObservationRead(BaseModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    run_id: uuid.UUID
    timestamp: datetime
    health_status: HealthStatus
    confidence: float
    observations: list[dict[str, Any]]
    consensus: dict[str, Any] | None = None
    image_refs: list[str] | None = None
    description: str | None = None
    companion_message: str | None = None
    created_at: datetime


class MilestoneRead(BaseModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    run_id: uuid.UUID | None = None
    timestamp: datetime
    event_type: str
    description: str
    resolved_at: datetime | None = None
    created_at: datetime
