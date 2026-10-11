"""Public published interface for the ``assessment`` bounded context."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assessment.models import Observation, PlantMilestone, TriggerResultRecord
from assessment.schemas import (
    HealthStatus,
    MilestoneRead,
    ObservationRead,
    TriggerDecision,
    TriggerResult,
)


def get_trigger_result(session: Session, run_id: uuid.UUID) -> TriggerResult | None:
    """Retrieve the deterministic trigger verdict for an orchestrator run."""
    stmt = select(TriggerResultRecord).where(TriggerResultRecord.run_id == run_id)
    rec = session.scalars(stmt).first()
    if not rec:
        return None
    return TriggerResult(
        decision=TriggerDecision(rec.decision),
        primary_symptom=rec.primary_symptom,
        confidence=rec.confidence,
        reasoning=rec.reasoning,
    )


def get_recent_observations(
    session: Session,
    plant_id: uuid.UUID,
    n: int = 7,
) -> list[ObservationRead]:
    """Retrieve the rolling N-day observation history (Tier 1 memory for companion)."""
    stmt = (
        select(Observation)
        .where(Observation.plant_id == plant_id)
        .order_by(Observation.timestamp.desc())
        .limit(n)
    )
    rows = list(session.scalars(stmt).all())
    # Return chronological order (oldest to newest)
    rows.reverse()
    return [
        ObservationRead(
            id=r.id,
            plant_id=r.plant_id,
            run_id=r.run_id,
            timestamp=r.timestamp,
            health_status=HealthStatus(r.health_status),
            confidence=r.confidence,
            observations=r.observations_json if isinstance(r.observations_json, list) else [],
            consensus=r.consensus_json,
            image_refs=r.image_refs_json,
            description=r.description,
            companion_message=r.companion_message,
            created_at=r.created_at,
        )
        for r in rows
    ]


def get_previous_observation(
    session: Session,
    plant_id: uuid.UUID,
) -> ObservationRead | None:
    """Retrieve the most recent observation prior to current run."""
    stmt = (
        select(Observation)
        .where(Observation.plant_id == plant_id)
        .order_by(Observation.timestamp.desc())
        .limit(1)
    )
    r = session.scalars(stmt).first()
    if not r:
        return None
    return ObservationRead(
        id=r.id,
        plant_id=r.plant_id,
        run_id=r.run_id,
        timestamp=r.timestamp,
        health_status=HealthStatus(r.health_status),
        confidence=r.confidence,
        observations=r.observations_json if isinstance(r.observations_json, list) else [],
        consensus=r.consensus_json,
        image_refs=r.image_refs_json,
        description=r.description,
        companion_message=r.companion_message,
        created_at=r.created_at,
    )


def get_milestones(
    session: Session,
    plant_id: uuid.UUID,
) -> list[MilestoneRead]:
    """Retrieve all recorded milestones for a plant (Tier 2 episodic memory)."""
    stmt = (
        select(PlantMilestone)
        .where(PlantMilestone.plant_id == plant_id)
        .order_by(PlantMilestone.timestamp.asc())
    )
    rows = session.scalars(stmt).all()
    return [
        MilestoneRead(
            id=r.id,
            plant_id=r.plant_id,
            run_id=r.run_id,
            timestamp=r.timestamp,
            event_type=r.event_type,
            description=r.description,
            resolved_at=r.resolved_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


def update_companion_message(
    session: Session,
    run_id: uuid.UUID,
    message: str,
) -> None:
    """Backfill the companion dialogue message on the observation record for a run."""
    stmt = select(Observation).where(Observation.run_id == run_id)
    obs = session.scalars(stmt).first()
    if obs:
        obs.companion_message = message
        session.commit()


def count_observations(session: Session, plant_id: uuid.UUID) -> int:
    """Retrieve total observation count for a plant (used for dayCount calculation)."""
    stmt = (
        select(func.count())
        .select_from(Observation)
        .where(Observation.plant_id == plant_id)
    )
    return session.scalar(stmt) or 0

