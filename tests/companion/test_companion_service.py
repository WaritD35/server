"""Tests for the companion service layer and public interface."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy.orm import Session

from advice.schemas import ActionType, CareAction, CarePlanRead
from assessment.models import Observation
from assessment.schemas import HealthStatus, TriggerDecision
from companion.interface import get_latest_message, get_message_by_run_id
from companion.service import (
    generate_for_plant,
    generate_info_request_message,
    generate_steady_message,
)


def test_generate_steady_message():
    normal_msg = generate_steady_message(None, None, "Ferny")
    assert "thriving" in normal_msg or "feeling great" in normal_msg


def test_generate_info_request_message():
    info_msg = generate_info_request_message("Ferny")
    assert "clear look" in info_msg or "photo" in info_msg


def test_generate_for_plant_no_action(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    obs = Observation(
        id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=run_id,
        timestamp=datetime.now(UTC),
        health_status=HealthStatus.HEALTHY.value,
        confidence=0.95,
        observations_json=[],
        consensus_json={},
        image_refs_json=[],
        created_at=datetime.now(UTC),
    )
    session.add(obs)
    session.commit()

    msg_read = generate_for_plant(
        session=session,
        plant_id=plant_id,
        run_id=run_id,
        decision=TriggerDecision.NO_ACTION,
        plant_nickname="Ferny",
    )

    assert msg_read.plant_id == plant_id
    assert msg_read.run_id == run_id
    assert msg_read.source == "template_steady"
    assert "Ferny" in msg_read.message or "feeling great" in msg_read.message

    # Verify backfill on observation
    session.refresh(obs)
    assert obs.companion_message == msg_read.message

    # Test interface
    latest = get_latest_message(session, plant_id)
    assert latest is not None
    assert latest.id == msg_read.id

    by_run = get_message_by_run_id(session, run_id)
    assert by_run is not None
    assert by_run.id == msg_read.id


def test_generate_for_plant_request_more_info(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    msg_read = generate_for_plant(
        session=session,
        plant_id=plant_id,
        run_id=run_id,
        decision=TriggerDecision.REQUEST_MORE_INFORMATION,
        plant_nickname="Ferny",
    )

    assert msg_read.source == "template_info_request"
    assert "photo" in msg_read.message


def test_generate_for_plant_care_advice_llm_success(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    care_plan = CarePlanRead(
        id=uuid.uuid4(),
        care_plan_id="cp_test3",
        plant_id=plant_id,
        run_id=run_id,
        status_label="Thirsty",
        assessment="Soil is completely dry.",
        confidence=0.9,
        actions=[
            CareAction(
                id="act_1",
                priority=1,
                action="Water thoroughly until drainage occurs",
                label="Water",
                type=ActionType.WATER,
            )
        ],
        created_at=datetime.now(UTC),
    )

    mock_llm_text = "I'm parched! Please give me a good water thoroughly until drainage occurs!"

    with patch(
        "companion.service.generate_companion_message",
        return_value=mock_llm_text,
    ):
        msg_read = generate_for_plant(
            session=session,
            plant_id=plant_id,
            run_id=run_id,
            decision=TriggerDecision.CARE_ADVICE_REQUIRED,
            care_plan=care_plan,
            plant_nickname="Ferny",
        )

    assert msg_read.source == "llm"
    assert msg_read.message == mock_llm_text


def test_generate_for_plant_care_advice_validation_fallback(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    care_plan = CarePlanRead(
        id=uuid.uuid4(),
        care_plan_id="cp_test4",
        plant_id=plant_id,
        run_id=run_id,
        status_label="Pest attack",
        assessment="Aphids present on leaves.",
        confidence=0.9,
        actions=[
            CareAction(
                id="act_1",
                priority=1,
                action="Spray neem oil insecticide",
                label="Neem spray",
                type=ActionType.INSPECT,
            )
        ],
        created_at=datetime.now(UTC),
    )

    # LLM drops neem oil action completely
    mock_llm_text = "I feel funny today, can you look at me?"

    with patch(
        "companion.service.generate_companion_message",
        return_value=mock_llm_text,
    ):
        msg_read = generate_for_plant(
            session=session,
            plant_id=plant_id,
            run_id=run_id,
            decision=TriggerDecision.CARE_ADVICE_REQUIRED,
            care_plan=care_plan,
            plant_nickname="Ferny",
        )

    assert msg_read.source == "template_fallback"
    assert "Recommended actions:" in msg_read.message
    assert "Spray neem oil" in msg_read.message
