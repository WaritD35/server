"""Integration tests for advice service layer."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from advice.interface import (
    get_care_plan,
    get_care_plan_actions,
    get_latest_care_plan_for_plant,
)
from advice.service import generate_for_plant
from assessment.models import Observation, TriggerResultRecord
from assessment.schemas import HealthStatus, TriggerDecision
from registry.models import Plant


def test_generate_advice_skipped_when_no_action(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    tr = TriggerResultRecord(
        run_id=run_id,
        plant_id=plant_id,
        decision=TriggerDecision.NO_ACTION.value,
        confidence=1.0,
    )
    session.add(tr)
    session.commit()

    plan = generate_for_plant(session, plant_id, run_id)
    assert plan is None


def test_generate_advice_creates_plan_on_care_advice_required(session: Session):
    user_id = uuid.uuid4()
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Plant
    plant = Plant(
        id=plant_id,
        owner_id=user_id,
        name="Monty",
        species_code="monstera_deliciosa",
    )
    session.add(plant)

    # Trigger verdict
    tr = TriggerResultRecord(
        run_id=run_id,
        plant_id=plant_id,
        decision=TriggerDecision.CARE_ADVICE_REQUIRED.value,
        primary_symptom="yellowing",
        confidence=0.95,
        reasoning="Health status dropped.",
    )
    session.add(tr)

    # Observation
    obs = Observation(
        id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=run_id,
        timestamp=datetime.now(UTC),
        health_status=HealthStatus.UNHEALTHY.value,
        confidence=0.95,
        observations_json=[{"type": "yellowing", "severity": "severe"}],
    )
    session.add(obs)
    session.commit()

    plan = generate_for_plant(session, plant_id, run_id)
    assert plan is not None
    assert plan.plant_id == plant_id
    assert plan.run_id == run_id
    # Assert normalized relational actions are created
    assert len(plan.actions) > 0
    assert plan.actions[0].action_id.startswith("act_")
    assert plan.actions[0].care_plan_pk == plan.id
    assert plan.actions[0].care_plan_id == plan.care_plan_id

    # Test published interface
    fetched = get_care_plan(session, run_id)
    assert fetched is not None
    assert fetched.care_plan_id == plan.care_plan_id
    assert len(fetched.actions) == len(plan.actions)
    assert fetched.actions[0].id == plan.actions[0].action_id

    latest = get_latest_care_plan_for_plant(session, plant_id)
    assert latest is not None
    assert latest.id == plan.id

    # Test dedicated get_care_plan_actions helper
    rel_actions = get_care_plan_actions(session, plan.care_plan_id)
    assert len(rel_actions) == len(plan.actions)
    assert rel_actions[0].action_id == plan.actions[0].action_id
