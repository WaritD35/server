"""Tests for the companion fact-preservation validator."""

import uuid
from datetime import UTC, datetime

from advice.schemas import ActionType, CareAction, CarePlanRead
from companion.validator import extract_keywords, validate_fact_preservation


def test_extract_keywords():
    text = "Please carefully water the plant and move it to bright indirect sunlight"
    kw = extract_keywords(text)
    assert "water" in kw
    assert "move" in kw
    assert "bright" in kw
    assert "indirect" in kw
    assert "sunlight" in kw
    assert "the" not in kw
    assert "plant" not in kw
    assert "carefully" not in kw


def test_validate_fact_preservation_success():
    care_plan = CarePlanRead(
        id=uuid.uuid4(),
        care_plan_id="cp_test1",
        plant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        status_label="Thirsty",
        assessment="Leaves are slightly wilting due to dry soil.",
        confidence=0.9,
        actions=[
            CareAction(
                id="act_1",
                priority=1,
                action="Water thoroughly until drainage occurs",
                label="Water plant",
                type=ActionType.WATER,
            ),
            CareAction(
                id="act_2",
                priority=2,
                action="Move away from drafty cold window",
                label="Move window",
                type=ActionType.MOVE,
            ),
        ],
        created_at=datetime.now(UTC),
    )

    companion_text = (
        "I'm feeling so thirsty! Please give me a thorough water until it starts draining, "
        "and could you move me away from this chilly cold window draft?"
    )

    is_valid, missing = validate_fact_preservation(care_plan, companion_text)
    assert is_valid is True
    assert missing == []


def test_validate_fact_preservation_dropped_action():
    care_plan = CarePlanRead(
        id=uuid.uuid4(),
        care_plan_id="cp_test2",
        plant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        status_label="Thirsty",
        assessment="Dry soil.",
        confidence=0.9,
        actions=[
            CareAction(
                id="act_1",
                priority=1,
                action="Water thoroughly until drainage occurs",
                label="Water plant",
                type=ActionType.WATER,
            ),
            CareAction(
                id="act_2",
                priority=2,
                action="Wipe leaves with neem oil solution",
                label="Wipe neem",
                type=ActionType.INSPECT,
            ),
        ],
        created_at=datetime.now(UTC),
    )

    companion_text = "I am parched! Please water me thoroughly until drainage occurs."

    is_valid, missing = validate_fact_preservation(care_plan, companion_text)
    assert is_valid is False
    assert len(missing) == 1
    assert "neem oil" in missing[0]
