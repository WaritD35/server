"""Pydantic schemas for the ``companion`` bounded context."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class CompanionMessageCreate(BaseModel):
    plant_id: uuid.UUID
    run_id: uuid.UUID
    decision: str
    message: str
    source: str


class CompanionMessageRead(BaseModel):
    id: uuid.UUID
    plant_id: uuid.UUID
    run_id: uuid.UUID
    decision: str
    message: str
    source: str
    created_at: datetime
