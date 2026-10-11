"""``assess_frames``: skip rules, the probe input, and the stored ``Observation``."""

import logging
import uuid
from collections.abc import Callable

import pytest
from assessment_fakes import CURRENT_CAPTURED_AT, FakeFrames, FakeProbe
from sqlalchemy import select
from sqlalchemy.orm import Session

from assessment.models import Observation
from assessment.schemas import HealthStatus
from assessment.service import AssessmentSkipped, assess_frames
from core.storage import bucket_names
from mlops.assessment import ProbeVerdict


def _observations(session: Session) -> list[Observation]:
    return list(session.scalars(select(Observation)).all())


def test_a_plant_without_species_is_skipped(
    session: Session,
    add_plant: Callable[..., uuid.UUID],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    plant_id = add_plant(species_code=None)
    frames.add_pair(plant_id)

    with pytest.raises(AssessmentSkipped, match="^species not set$"):
        assess_frames(session, plant_id, uuid.uuid4(), frames_run_id=None)

    assert probe.calls == []
    assert _observations(session) == []


def test_an_archived_plant_is_skipped(
    session: Session,
    add_plant: Callable[..., uuid.UUID],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    plant_id = add_plant(archived=True)
    frames.add_pair(plant_id)

    with pytest.raises(AssessmentSkipped, match="plant not found"):
        assess_frames(session, plant_id, uuid.uuid4(), frames_run_id=None)

    assert probe.calls == []
    assert _observations(session) == []


def test_an_unknown_plant_is_skipped(
    session: Session, frames: FakeFrames, probe: FakeProbe
) -> None:
    with pytest.raises(AssessmentSkipped, match="plant not found"):
        assess_frames(session, uuid.uuid4(), uuid.uuid4(), frames_run_id=None)

    assert probe.calls == []


def test_a_baseline_frame_without_a_previous_one_is_skipped(
    session: Session,
    plant_id: uuid.UUID,
    add_species: Callable[..., None],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    add_species()
    run_id = uuid.uuid4()

    with pytest.raises(AssessmentSkipped, match="^baseline frame stored; need two frames$"):
        assess_frames(session, plant_id, run_id, frames_run_id=run_id)

    assert frames.pair_calls == [(plant_id, run_id)]
    assert probe.calls == []
    assert _observations(session) == []


def test_happy_path_writes_one_observation_mapped_from_the_verdict(
    session: Session,
    plant_id: uuid.UUID,
    add_species: Callable[..., None],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    add_species(knowledge="Keep the soil moist.")
    pair = frames.add_pair(plant_id)
    run_id = uuid.uuid4()

    returned = assess_frames(session, plant_id, run_id, frames_run_id=run_id)

    assert frames.pair_calls == [(plant_id, run_id)]
    (sent,) = probe.calls
    assert sent.plant_id == str(plant_id)
    assert sent.species == "Spathiphyllum wallisii"
    assert sent.plant_knowledge == "Keep the soil moist."
    assert sent.previous_image == f"jpeg:{pair.previous.object_key}".encode()
    assert sent.current_image == f"jpeg:{pair.current.object_key}".encode()

    (stored,) = _observations(session)
    assert stored.id == returned.id
    assert stored.plant_id == plant_id
    assert stored.run_id == run_id
    assert stored.timestamp.replace(tzinfo=CURRENT_CAPTURED_AT.tzinfo) == CURRENT_CAPTURED_AT
    assert stored.health_status == HealthStatus.POSSIBLY_UNHEALTHY.value
    assert stored.confidence == 0.8
    assert stored.observations_json == [
        {"type": "leaf_yellowing", "severity": "moderate", "description": "Lower leaf"}
    ]
    assert stored.consensus_json == {"agreement": 0.8, "runs": 5, "model_stated_average": 0.72}
    bucket = bucket_names()["frames_derived"]
    assert stored.image_refs_json == [
        f"s3://{bucket}/{pair.previous.object_key}",
        f"s3://{bucket}/{pair.current.object_key}",
    ]
    assert stored.description is None
    assert stored.companion_message is None


def test_missing_species_entry_and_knowledge_fall_back_with_warnings(
    session: Session,
    plant_id: uuid.UUID,
    frames: FakeFrames,
    probe: FakeProbe,
    caplog: pytest.LogCaptureFixture,
) -> None:
    frames.add_pair(plant_id)

    with caplog.at_level(logging.WARNING, logger="assessment.service"):
        assess_frames(session, plant_id, uuid.uuid4(), frames_run_id=None)

    (sent,) = probe.calls
    assert sent.species == "spath"
    assert sent.plant_knowledge == ""
    assert frames.pair_calls == [(plant_id, None)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("species" in m and "spath" in m for m in warnings)
    assert any("knowledge" in m and "spath" in m for m in warnings)
    assert len(_observations(session)) == 1


def test_an_unknown_health_status_is_rejected_and_nothing_is_stored(
    session: Session,
    plant_id: uuid.UUID,
    add_species: Callable[..., None],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    add_species()
    frames.add_pair(plant_id)
    probe.verdict = ProbeVerdict(
        health_status="possible_unhealthy",
        observations=[],
        consensus={"agreement": 1.0, "runs": 5, "model_stated_average": None},
    )

    with pytest.raises(ValueError, match="possible_unhealthy"):
        assess_frames(session, plant_id, uuid.uuid4(), frames_run_id=None)

    assert _observations(session) == []
