"""Unit tests for deterministic milestone streak detection."""

import uuid
from datetime import UTC, datetime, timedelta

from assessment.models import Observation, PlantMilestone
from assessment.schemas import HealthStatus, MilestoneType
from assessment.service import detect_milestones


def make_obs(
    plant_id: uuid.UUID,
    dt: datetime,
    health_status: str,
    symptoms: list[dict] | None = None,
) -> Observation:
    return Observation(
        id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=uuid.uuid4(),
        timestamp=dt,
        health_status=health_status,
        confidence=1.0,
        observations_json=symptoms or [],
    )


def test_first_symptom_milestone():
    plant_id = uuid.uuid4()
    now = datetime.now(UTC)
    obs = make_obs(
        plant_id,
        now,
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "leaf_burn", "severity": "mild"}],
    )
    detected = detect_milestones(obs, history=[], existing_milestones=[])
    assert len(detected) == 1
    assert detected[0].event_type == MilestoneType.FIRST_SYMPTOM.value
    assert "leaf_burn" in detected[0].description


def test_health_crisis_milestone():
    plant_id = uuid.uuid4()
    t1 = datetime.now(UTC) - timedelta(days=1)
    t2 = datetime.now(UTC)
    h1 = make_obs(plant_id, t1, HealthStatus.HEALTHY.value)
    h2 = make_obs(plant_id, t2, HealthStatus.UNHEALTHY.value)

    detected = detect_milestones(h2, history=[h1], existing_milestones=[])
    types = [d.event_type for d in detected]
    assert MilestoneType.HEALTH_CRISIS.value in types


def test_severe_episode_three_day_streak():
    plant_id = uuid.uuid4()
    base_time = datetime.now(UTC)
    h1 = make_obs(plant_id, base_time - timedelta(days=2), HealthStatus.UNHEALTHY.value)
    h2 = make_obs(plant_id, base_time - timedelta(days=1), HealthStatus.UNHEALTHY.value)
    h3 = make_obs(plant_id, base_time, HealthStatus.UNHEALTHY.value)

    crisis = PlantMilestone(
        id=uuid.uuid4(),
        plant_id=plant_id,
        timestamp=base_time - timedelta(days=2),
        event_type=MilestoneType.HEALTH_CRISIS.value,
        description="Crisis",
    )

    detected = detect_milestones(h3, history=[h1, h2], existing_milestones=[crisis])
    types = [d.event_type for d in detected]
    assert MilestoneType.SEVERE_EPISODE.value in types


def test_full_recovery_milestone():
    plant_id = uuid.uuid4()
    base_time = datetime.now(UTC)
    h1 = make_obs(plant_id, base_time - timedelta(days=1), HealthStatus.UNHEALTHY.value)
    h2 = make_obs(plant_id, base_time, HealthStatus.HEALTHY.value)

    crisis = PlantMilestone(
        id=uuid.uuid4(),
        plant_id=plant_id,
        timestamp=base_time - timedelta(days=5),
        event_type=MilestoneType.HEALTH_CRISIS.value,
        description="Crisis",
    )

    detected = detect_milestones(h2, history=[h1], existing_milestones=[crisis])
    types = [d.event_type for d in detected]
    assert MilestoneType.FULL_RECOVERY.value in types
