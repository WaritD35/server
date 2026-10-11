"""Public published interface for the ``companion`` bounded context."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from companion.models import MessageRecord
from companion.schemas import CompanionMessageRead


def get_latest_message(
    session: Session,
    plant_id: uuid.UUID,
) -> CompanionMessageRead | None:
    """Retrieve the most recent companion dialogue message for a plant."""
    stmt = (
        select(MessageRecord)
        .where(MessageRecord.plant_id == plant_id)
        .order_by(MessageRecord.created_at.desc())
        .limit(1)
    )
    r = session.scalars(stmt).first()
    if not r:
        return None
    return CompanionMessageRead(
        id=r.id,
        plant_id=r.plant_id,
        run_id=r.run_id,
        decision=r.decision,
        message=r.message,
        source=r.source,
        created_at=r.created_at,
    )


def get_message_by_run_id(
    session: Session,
    run_id: uuid.UUID,
) -> CompanionMessageRead | None:
    """Retrieve the companion dialogue message generated for a specific pipeline run."""
    stmt = select(MessageRecord).where(MessageRecord.run_id == run_id)
    r = session.scalars(stmt).first()
    if not r:
        return None
    return CompanionMessageRead(
        id=r.id,
        plant_id=r.plant_id,
        run_id=r.run_id,
        decision=r.decision,
        message=r.message,
        source=r.source,
        created_at=r.created_at,
    )
