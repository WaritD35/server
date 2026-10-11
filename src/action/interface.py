"""Public published interface for the ``action`` bounded context."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from action.models import CareEvent


def get_last_watered_at(session: Session, plant_id: uuid.UUID) -> datetime | None:
    """Retrieve timestamp of the most recent 'watered' care event for a plant."""
    stmt = (
        select(CareEvent.occurred_at)
        .where(CareEvent.plant_id == plant_id, CareEvent.event_type == "watered")
        .order_by(CareEvent.occurred_at.desc())
        .limit(1)
    )
    return session.scalar(stmt)
