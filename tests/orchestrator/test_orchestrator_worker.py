"""The queue end to end: scheduling and stage order."""

import uuid
from datetime import timedelta

import orchestrator_fakes as fakes
from orchestrator_fakes import JST_0705, RunTick
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orchestrator import service
from orchestrator.config import OrchestratorConfig
from orchestrator.models import PipelineRun
from orchestrator.stages import StageSkipped
from orchestrator.worker import tick

ORDER = ["capture", "assessment", "advice", "companion"]


def _run_of(session_factory: sessionmaker[Session], plant_id: uuid.UUID) -> PipelineRun:
    with session_factory() as session:
        return session.scalars(select(PipelineRun).where(PipelineRun.plant_id == plant_id)).one()


def test_each_plant_goes_through_every_stage_in_order(
    run_tick: RunTick, session_factory: sessionmaker[Session], plants: list[uuid.UUID]
) -> None:
    report = run_tick(JST_0705)

    assert report.queued == 2
    assert report.finished == {"succeeded": 2}
    # one plant finishes before the next starts
    assert fakes.CALLS == [(s, plants[0]) for s in ORDER] + [(s, plants[1]) for s in ORDER]
    for plant_id in plants:
        run = _run_of(session_factory, plant_id)
        assert run.status == "succeeded"
        assert run.current_stage == "companion"
        assert run.started_at is not None and run.finished_at is not None


def test_a_slot_is_queued_once(run_tick: RunTick) -> None:
    run_tick(JST_0705)
    again = run_tick(JST_0705 + timedelta(minutes=1))
    assert again.queued == 0
    assert again.finished == {}


def test_nothing_is_queued_outside_the_schedule(run_tick: RunTick) -> None:
    late = JST_0705 + timedelta(hours=2)  # 09:05 JST: past the 60-minute catch-up
    assert run_tick(late).queued == 0
    assert fakes.CALLS == []


def test_a_failing_stage_fails_only_that_run(
    run_tick: RunTick, session_factory: sessionmaker[Session], plants: list[uuid.UUID]
) -> None:
    fakes.PLAN[("assessment", plants[0])] = [RuntimeError("VLM unreachable")]

    report = run_tick(JST_0705)

    assert report.finished == {"failed": 1, "succeeded": 1}
    run = _run_of(session_factory, plants[0])
    assert run.status == "failed"
    assert run.current_stage == "assessment"
    assert "VLM unreachable" in (run.detail or "")
    assert ("advice", plants[0]) not in fakes.CALLS
    assert _run_of(session_factory, plants[1]).status == "succeeded"
    # not retried
    run_tick(JST_0705 + timedelta(minutes=10))
    assert fakes.CALLS.count(("assessment", plants[0])) == 1


def test_a_run_left_running_by_a_stopped_process_is_failed(
    run_tick: RunTick, session_factory: sessionmaker[Session], plants: list[uuid.UUID]
) -> None:
    now = JST_0705 + timedelta(hours=2)  # past the catch-up window: nothing new queued
    with session_factory() as session:
        stale = PipelineRun(
            plant_id=plants[0],
            scheduled_for=now - timedelta(hours=5),
            status="running",
            current_stage="assessment",
            started_at=now - timedelta(minutes=61),
        )
        live = PipelineRun(
            plant_id=plants[1],
            scheduled_for=now - timedelta(hours=5),
            status="running",
            current_stage="capture",
            started_at=now - timedelta(minutes=5),
        )
        session.add_all([stale, live])
        session.commit()

    report = run_tick(now)

    assert report.abandoned == 1
    stale_run = _run_of(session_factory, plants[0])
    assert stale_run.status == "failed"
    assert stale_run.current_stage == "assessment"
    assert (stale_run.detail or "").startswith("assessment: abandoned")
    assert stale_run.finished_at is not None
    # a run another process is still executing is left alone
    assert _run_of(session_factory, plants[1]).status == "running"


def test_skip_ends_the_run(
    run_tick: RunTick, session_factory: sessionmaker[Session], plants: list[uuid.UUID]
) -> None:
    fakes.PLAN[("capture", plants[0])] = [StageSkipped("no new frames")]

    run_tick(JST_0705)
    run = _run_of(session_factory, plants[0])
    assert run.status == "skipped"
    assert run.detail == "capture: no new frames"
    assert ("assessment", plants[0]) not in fakes.CALLS


def test_a_run_is_claimed_once(
    session_factory: sessionmaker[Session], config: OrchestratorConfig, plants: list[uuid.UUID]
) -> None:
    with session_factory() as session:
        service.enqueue_due(session, config, JST_0705, plants[:1])
    with session_factory() as first, session_factory() as second:
        assert service.claim_next(first, JST_0705) is not None
        assert service.claim_next(second, JST_0705) is None


def test_should_stop_leaves_queued_runs_for_later(
    session_factory: sessionmaker[Session], config: OrchestratorConfig, plants: list[uuid.UUID]
) -> None:
    report = tick(
        session_factory,
        config,
        now=lambda: JST_0705,
        plant_ids=lambda _s: plants,
        stages=fakes.FAKE_STAGES,
        should_stop=lambda: True,
    )
    assert report.queued == 2
    assert report.finished == {}
    assert fakes.CALLS == []
