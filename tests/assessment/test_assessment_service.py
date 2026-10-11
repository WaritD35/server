"""Integration tests for assessment service layer."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from assessment.interface import get_recent_observations, get_trigger_result
from assessment.models import Observation
from assessment.schemas import HealthStatus, TriggerDecision
from assessment.service import evaluate_observation


def test_evaluate_observation_pipeline(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    obs = Observation(
        id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=run_id,
        timestamp=datetime.now(UTC),
        health_status=HealthStatus.POSSIBLY_UNHEALTHY.value,
        confidence=0.92,
        observations_json=[{"type": "wilting", "severity": "mild"}],
    )
    session.add(obs)
    session.commit()

    record = evaluate_observation(session, plant_id, run_id)
    assert record.decision == TriggerDecision.CARE_ADVICE_REQUIRED.value
    assert record.primary_symptom == "wilting"

    # Verify published interface reads
    tr = get_trigger_result(session, run_id)
    assert tr is not None
    assert tr.decision == TriggerDecision.CARE_ADVICE_REQUIRED

    history = get_recent_observations(session, plant_id, n=7)
    assert len(history) == 1
    assert history[0].health_status == HealthStatus.POSSIBLY_UNHEALTHY
