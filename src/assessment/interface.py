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


def _observation_read(row: Observation) -> ObservationRead:
    return ObservationRead(
        id=row.id,
        plant_id=row.plant_id,
        run_id=row.run_id,
        timestamp=row.timestamp,
        health_status=HealthStatus(row.health_status),
        confidence=row.confidence,
        observations=row.observations_json if isinstance(row.observations_json, list) else [],
        consensus=row.consensus_json,
        image_refs=row.image_refs_json,
        description=row.description,
        companion_message=row.companion_message,
        created_at=row.created_at,
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
    return [_observation_read(row) for row in rows]


def get_observation_by_run(session: Session, run_id: uuid.UUID) -> ObservationRead | None:
    """The observation stored for one orchestrator or debug run."""
    row = session.scalars(select(Observation).where(Observation.run_id == run_id)).first()
    if row is None:
        return None
    return _observation_read(row)


def get_previous_observation(
    session: Session,
    plant_id: uuid.UUID,
    excluding_run_id: uuid.UUID | None = None,
) -> ObservationRead | None:
    """The latest observation for ``plant_id``.

    Pass ``excluding_run_id`` to skip that run. The companion stage does this
    so the observation just stored is not reported as the previous one. Callers
    that want the latest row, including a debug observation, omit it.
    """
    stmt = select(Observation).where(Observation.plant_id == plant_id)
    if excluding_run_id is not None:
        stmt = stmt.where(Observation.run_id != excluding_run_id)
    row = session.scalars(stmt.order_by(Observation.timestamp.desc()).limit(1)).first()
    if row is None:
        return None
    return _observation_read(row)


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

