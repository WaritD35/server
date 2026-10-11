"""End-to-end integration tests for the full orchestrator pipeline across all 3 branches."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from action.db import Base as ActionBase
from advice import models as advice_models  # noqa: F401 - register tables
from advice.db import Base as AdviceBase
from advice.interface import get_care_plan
from assessment import models as assessment_models  # noqa: F401 - register tables
from assessment.db import Base as AssessmentBase
from assessment.interface import get_trigger_result
from assessment.models import Observation
from assessment.schemas import HealthStatus, TriggerDecision
from companion import models as companion_models  # noqa: F401 - register tables
from companion.db import Base as CompanionBase
from companion.interface import get_message_by_run_id
from core.db import Base as AuthBase
from knowledge import models as knowledge_models  # noqa: F401 - register tables
from knowledge.db import Base as KnowledgeBase
from orchestrator.db import Base as OrchestratorBase
from orchestrator.models import PipelineRun
from orchestrator.stages import STAGES
from orchestrator.worker import execute_run
from registry import models as registry_models  # noqa: F401 - register tables
from registry.db import Base as RegistryBase
from registry.models import Plant

_BASES: tuple[type[DeclarativeBase], ...] = (
    AuthBase,
    RegistryBase,
    KnowledgeBase,
    AssessmentBase,
    AdviceBase,
    ActionBase,
    CompanionBase,
    OrchestratorBase,
)


@pytest.fixture
def e2e_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(
        schema_translate_map={
            "auth": None,
            "registry": None,
            "knowledge": None,
            "orchestrator": None,
            "assessment": None,
            "advice": None,
            "action": None,
            "companion": None,
        }
    )
    for base in _BASES:
        base.metadata.create_all(engine)
    try:
        sm = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        yield sm
    finally:
        for base in reversed(_BASES):
            base.metadata.drop_all(engine)
        engine.dispose()


def test_pipeline_branch_healthy_no_action(e2e_session_factory: sessionmaker[Session]):
    """Branch 1: Healthy observation -> NO_ACTION -> Advice None -> Companion steady."""
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)

    with e2e_session_factory() as session:
        # Create registered plant
        plant = Plant(
            id=plant_id,
            owner_id=uuid.uuid4(),
            name="Monstera Deliciosa",
            species_code="monstera_deliciosa",
        )
        session.add(plant)

        # Create observation for this run
        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant_id,
            run_id=run_id,
            timestamp=now,
            health_status=HealthStatus.HEALTHY.value,
            confidence=0.95,
            observations_json=[],
            consensus_json={},
            image_refs_json=[],
            created_at=now,
        )
        session.add(obs)

        # Create pipeline run
        run = PipelineRun(
            id=run_id,
            plant_id=plant_id,
            scheduled_for=now,
            status="running",
            current_stage="capture",
        )
        session.add(run)
        session.commit()

        # Execute all stages
        with patch("orchestrator.stages.session_factory", e2e_session_factory):
            status = execute_run(session, run, stages=STAGES)

        assert status == "succeeded"

        # Check TriggerResult
        trig = get_trigger_result(session, run_id)
        assert trig is not None
        assert trig.decision == TriggerDecision.NO_ACTION

        # Check Advice (should NOT have generated care plan)
        care_plan = get_care_plan(session, run_id)
        assert care_plan is None

        # Check Companion message
        comp_msg = get_message_by_run_id(session, run_id)
        assert comp_msg is not None
        assert comp_msg.source == "template_steady"
        assert "thriving" in comp_msg.message or "feeling great" in comp_msg.message


def test_pipeline_branch_low_confidence_request_info(
    e2e_session_factory: sessionmaker[Session],
):
    """Branch 2: Low-confidence observation (< 0.5) -> REQUEST_MORE_INFORMATION -> Photo prompt."""
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)

    with e2e_session_factory() as session:
        plant = Plant(
            id=plant_id,
            owner_id=uuid.uuid4(),
            name="Fiddle Leaf Fig",
            species_code="ficus_lyrata",
        )
        session.add(plant)

        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant_id,
            run_id=run_id,
            timestamp=now,
            health_status=HealthStatus.UNHEALTHY.value,
            confidence=0.35,  # Low confidence triggers photo request
            observations_json=[{"type": "leaf_spot", "severity": "mild"}],
            consensus_json={},
            image_refs_json=[],
            created_at=now,
        )
        session.add(obs)

        run = PipelineRun(
            id=run_id,
            plant_id=plant_id,
            scheduled_for=now,
            status="running",
            current_stage="capture",
        )
        session.add(run)
        session.commit()

        with patch("orchestrator.stages.session_factory", e2e_session_factory):
            status = execute_run(session, run, stages=STAGES)

        assert status == "succeeded"

        trig = get_trigger_result(session, run_id)
        assert trig is not None
        assert trig.decision == TriggerDecision.REQUEST_MORE_INFORMATION

        care_plan = get_care_plan(session, run_id)
        assert care_plan is None

        comp_msg = get_message_by_run_id(session, run_id)
        assert comp_msg is not None
        assert comp_msg.source == "template_info_request"
        assert "photo" in comp_msg.message or "blurry" in comp_msg.message


def test_pipeline_branch_unhealthy_care_advice(
    e2e_session_factory: sessionmaker[Session],
):
    """Branch 3: Unhealthy observation -> CARE_ADVICE_REQUIRED -> CarePlan & Companion dialogue."""
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)

    with e2e_session_factory() as session:
        plant = Plant(
            id=plant_id,
            owner_id=uuid.uuid4(),
            name="Pothos",
            species_code="epipremnum_aureum",
        )
        session.add(plant)

        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant_id,
            run_id=run_id,
            timestamp=now,
            health_status=HealthStatus.UNHEALTHY.value,
            confidence=0.92,
            observations_json=[{"type": "wilting", "severity": "moderate"}],
            consensus_json={},
            image_refs_json=[],
            created_at=now,
        )
        session.add(obs)

        run = PipelineRun(
            id=run_id,
            plant_id=plant_id,
            scheduled_for=now,
            status="running",
            current_stage="capture",
        )
        session.add(run)
        session.commit()

        with patch("orchestrator.stages.session_factory", e2e_session_factory):
            status = execute_run(session, run, stages=STAGES)

        assert status == "succeeded"

        trig = get_trigger_result(session, run_id)
        assert trig is not None
        assert trig.decision == TriggerDecision.CARE_ADVICE_REQUIRED

        care_plan = get_care_plan(session, run_id)
        assert care_plan is not None
        assert len(care_plan.actions) > 0

        from advice.interface import get_care_plan_actions
        rel_actions = get_care_plan_actions(session, care_plan.care_plan_id)
        assert len(rel_actions) == len(care_plan.actions)

        comp_msg = get_message_by_run_id(session, run_id)
        assert comp_msg is not None
        assert comp_msg.source in ("llm", "template_fallback")
        assert len(comp_msg.message) > 10
