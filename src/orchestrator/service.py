"""The job queue: work out due slots, queue runs, hand them out, record the end.

No HTTP and no stage execution here — :mod:`orchestrator.worker` drives these
functions.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.config import OrchestratorConfig
from orchestrator.models import PipelineRun
from orchestrator.stages import STAGES, Stage

# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #


def _slot(day: date, at: time, config: OrchestratorConfig) -> datetime:
    return datetime.combine(day, at, tzinfo=config.timezone).astimezone(UTC)


def due_slots(config: OrchestratorConfig, now: datetime) -> list[datetime]:
    """Schedule slots (UTC) that have arrived and are still within ``catch_up``.

    Yesterday is included so a slot shortly before midnight is still caught just
    after it.
    """
    today = now.astimezone(config.timezone).date()
    return [
        slot
        for day in (today - timedelta(days=1), today)
        for at in config.schedule
        if (slot := _slot(day, at, config)) <= now < slot + config.catch_up
    ]


def next_slot(config: OrchestratorConfig, now: datetime) -> datetime | None:
    today = now.astimezone(config.timezone).date()
    upcoming = (
        _slot(day, at, config)
        for day in (today, today + timedelta(days=1))
        for at in config.schedule
    )
    return min((s for s in upcoming if s > now), default=None)


# --------------------------------------------------------------------------- #
# queue
# --------------------------------------------------------------------------- #


def enqueue_run(
    session: Session,
    plant_id: uuid.UUID,
    scheduled_for: datetime,
    *,
    trigger: str = "schedule",
    stages: Sequence[Stage] = STAGES,
) -> PipelineRun | None:
    """Queue a run for the plant and slot. ``None`` if one is already there."""
    exists = session.scalar(
        select(PipelineRun.id).where(
            PipelineRun.plant_id == plant_id, PipelineRun.scheduled_for == scheduled_for
        )
    )
    if exists is not None:
        return None
    run = PipelineRun(
        plant_id=plant_id,
        scheduled_for=scheduled_for,
        trigger=trigger,
        current_stage=stages[0].name,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        # Another process queued the same slot between our check and insert.
        session.rollback()
        return None
    return run


def enqueue_due(
    session: Session,
    config: OrchestratorConfig,
    now: datetime,
    plant_ids: Iterable[uuid.UUID],
    *,
    stages: Sequence[Stage] = STAGES,
) -> int:
    """Queue a run for every plant for every due slot not queued yet."""
    slots = due_slots(config, now)
    if not slots:
        return 0
    return sum(
        enqueue_run(session, plant_id, slot, stages=stages) is not None
        for plant_id in plant_ids
        for slot in slots
    )


def claim_next(session: Session, now: datetime) -> PipelineRun | None:
    """Take the oldest queued run and mark it running.

    The conditional UPDATE means two web processes never take the same run.
    """
    while True:
        run_id = session.scalar(
            select(PipelineRun.id)
            .where(PipelineRun.status == "queued")
            .order_by(PipelineRun.scheduled_for, PipelineRun.created_at, PipelineRun.id)
            .limit(1)
        )
        if run_id is None:
            return None
        claimed = session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == run_id, PipelineRun.status == "queued")
            .values(status="running", started_at=now)
            .execution_options(synchronize_session=False)
        )
        session.commit()
        if claimed.rowcount == 1:  # type: ignore[attr-defined]
            return session.get(PipelineRun, run_id, populate_existing=True)


def finish_run(
    session: Session,
    run: PipelineRun,
    status: str,
    now: datetime,
    *,
    detail: str | None = None,
) -> None:
    run.status = status
    run.detail = detail
    run.finished_at = now
    session.commit()


def fail_stale_runs(session: Session, now: datetime, stale_after: timedelta) -> int:
    """Mark runs still ``running`` ``stale_after`` past their start as ``failed``.

    A run only stays ``running`` when the process executing it stopped
    mid-stage; nothing else would ever finish it. Returns how many were marked.
    """
    stale = session.scalars(
        select(PipelineRun).where(
            PipelineRun.status == "running", PipelineRun.started_at < now - stale_after
        )
    ).all()
    minutes = int(stale_after.total_seconds() // 60)
    for run in stale:
        run.status = "failed"
        run.finished_at = now
        run.detail = (
            f"{run.current_stage}: abandoned — still running {minutes} min after it "
            "started (the process executing it probably stopped)"
        )
    session.commit()
    return len(stale)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def get_run(session: Session, run_id: uuid.UUID) -> PipelineRun | None:
    return session.get(PipelineRun, run_id)


def list_runs(
    session: Session,
    *,
    status: str | None = None,
    plant_id: uuid.UUID | None = None,
    limit: int = 50,
) -> Sequence[PipelineRun]:
    """Newest first."""
    stmt = (
        select(PipelineRun)
        .order_by(PipelineRun.scheduled_for.desc(), PipelineRun.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(PipelineRun.status == status)
    if plant_id is not None:
        stmt = stmt.where(PipelineRun.plant_id == plant_id)
    return session.scalars(stmt).all()
