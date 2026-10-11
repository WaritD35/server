"""End-to-end integration tests for the full orchestrator pipeline across all 3 branches."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
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
from capture import models as capture_models  # noqa: F401 - register tables
from capture.db import Base as CaptureBase
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
    CaptureBase,
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
            "vision": None,
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


def _ok_capture(*_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(quality_passed=True, quality_reason=None)


def _write_observation(
    health_status: str,
    confidence: float,
    observations: list[dict[str, str]],
):
    def assess_frames(
        session: Session,
        plant_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        frames_run_id: uuid.UUID | None,
    ) -> Observation:
        now = datetime.now(UTC)
        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant_id,
            run_id=run_id,
            timestamp=now,
            health_status=health_status,
            confidence=confidence,
            observations_json=observations,
            consensus_json={},
            image_refs_json=[],
            created_at=now,
        )
        session.add(obs)
        session.commit()
        return obs

    return assess_frames


def _add_plant_and_run(
    session: Session,
    *,
    name: str,
    species_code: str | None,
    device_id: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add(
        Plant(
            id=plant_id,
            owner_id=uuid.uuid4(),
            name=name,
            species_code=species_code,
            device_id=device_id,
        )
    )
    session.add(
        PipelineRun(
            id=run_id,
            plant_id=plant_id,
            scheduled_for=now,
            status="running",
            current_stage="capture",
        )
    )
    session.commit()
    return plant_id, run_id


def test_pipeline_branch_healthy_no_action(e2e_session_factory: sessionmaker[Session]):
    """Branch 1: Healthy observation -> NO_ACTION -> Advice None -> Companion steady."""
    with e2e_session_factory() as session:
        plant_id, run_id = _add_plant_and_run(
            session, name="Monstera Deliciosa", species_code="monstera_deliciosa"
        )
        run = session.get(PipelineRun, run_id)
        assert run is not None

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
            patch(
                "orchestrator.stages.assess_frames",
                _write_observation(HealthStatus.HEALTHY.value, 0.95, []),
            ),
        ):
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
        stored = session.scalars(select(Observation).where(Observation.run_id == run_id)).one()
        assert stored.plant_id == plant_id
        assert stored.health_status == HealthStatus.HEALTHY.value


def test_companion_previous_observation_excludes_this_run(
    e2e_session_factory: sessionmaker[Session],
):
    """The observation just stored is current, not the previous one."""
    with e2e_session_factory() as session:
        plant_id, run_id = _add_plant_and_run(session, name="Pothos", species_code="epipremnum")
        older_run = uuid.uuid4()
        now = datetime.now(UTC)
        session.add(
            Observation(
                plant_id=plant_id,
                run_id=older_run,
                timestamp=now - timedelta(hours=1),
                health_status=HealthStatus.HEALTHY.value,
                confidence=1.0,
                observations_json=[],
                consensus_json={},
                image_refs_json=[],
                created_at=now - timedelta(hours=1),
            )
        )
        session.commit()
        run = session.get(PipelineRun, run_id)
        assert run is not None
        seen: dict[str, uuid.UUID | None] = {}

        def spy(**kwargs: object) -> object:
            from companion.service import generate_for_plant

            previous = kwargs["previous_observation"]
            current = kwargs["observation"]
            seen["previous"] = getattr(previous, "run_id", None)
            seen["current"] = getattr(current, "run_id", None)
            return generate_for_plant(**kwargs)  # type: ignore[arg-type]

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
            patch(
                "orchestrator.stages.assess_frames",
                _write_observation(HealthStatus.HEALTHY.value, 0.95, []),
            ),
            patch("orchestrator.stages.generate_companion_message", spy),
        ):
            status = execute_run(session, run, stages=STAGES)

        assert status == "succeeded"
        assert seen["current"] == run_id
        assert seen["previous"] == older_run


def test_an_invalid_health_status_fails_the_run(
    e2e_session_factory: sessionmaker[Session],
):
    with e2e_session_factory() as session:
        _plant_id, run_id = _add_plant_and_run(session, name="Pothos", species_code="epipremnum")
        run = session.get(PipelineRun, run_id)
        assert run is not None

        def bad_rules(*_args: object, **_kwargs: object) -> None:
            raise ValueError("not a health status")

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
            patch(
                "orchestrator.stages.assess_frames",
                _write_observation(HealthStatus.HEALTHY.value, 0.95, []),
            ),
            patch("orchestrator.stages.evaluate_observation", bad_rules),
        ):
            status = execute_run(session, run, stages=STAGES)

        assert status == "failed"


def test_pipeline_branch_low_confidence_request_info(
    e2e_session_factory: sessionmaker[Session],
):
    """Branch 2: Low-confidence observation (< 0.5) -> REQUEST_MORE_INFORMATION -> Photo prompt."""
    with e2e_session_factory() as session:
        _plant_id, run_id = _add_plant_and_run(
            session, name="Fiddle Leaf Fig", species_code="ficus_lyrata"
        )
        run = session.get(PipelineRun, run_id)
        assert run is not None

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
            patch(
                "orchestrator.stages.assess_frames",
                _write_observation(
                    HealthStatus.UNHEALTHY.value,
                    0.35,
                    [{"type": "leaf_spot", "severity": "mild"}],
                ),
            ),
        ):
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
    with e2e_session_factory() as session:
        _plant_id, run_id = _add_plant_and_run(
            session, name="Pothos", species_code="epipremnum_aureum"
        )
        run = session.get(PipelineRun, run_id)
        assert run is not None

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
            patch(
                "orchestrator.stages.assess_frames",
                _write_observation(
                    HealthStatus.UNHEALTHY.value,
                    0.92,
                    [{"type": "wilting", "severity": "moderate"}],
                ),
            ),
        ):
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


def test_pipeline_skips_when_webcam_map_is_empty(
    e2e_session_factory: sessionmaker[Session],
):
    """Empty CAPTURE_WEBCAM_MAP skips capture; the run is skipped, not failed."""
    with e2e_session_factory() as session:
        _plant_id, run_id = _add_plant_and_run(
            session,
            name="Unmapped Plant",
            species_code="monstera_deliciosa",
            device_id="cam-1",
        )
        run = session.get(PipelineRun, run_id)
        assert run is not None

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("capture.service.get_settings") as settings,
        ):
            settings.return_value.capture_webcam_map = ""
            status = execute_run(session, run, stages=STAGES)

        assert status == "skipped"
        assert run.current_stage == "capture"
        assert "CAPTURE_WEBCAM_MAP" in (run.detail or "")
        assert get_trigger_result(session, run_id) is None


def test_pipeline_skips_when_assessment_has_no_baseline_frame(
    e2e_session_factory: sessionmaker[Session],
):
    """Fewer than two processed frames: assess_frames raises AssessmentSkipped."""
    with e2e_session_factory() as session:
        _plant_id, run_id = _add_plant_and_run(
            session, name="Baseline Plant", species_code="spath"
        )
        run = session.get(PipelineRun, run_id)
        assert run is not None

        with (
            patch("orchestrator.stages.session_factory", e2e_session_factory),
            patch("orchestrator.stages.capture_for_plant", _ok_capture),
        ):
            status = execute_run(session, run, stages=STAGES)

        assert status == "skipped"
        assert run.current_stage == "assessment"
        assert "two frames" in (run.detail or "")
        assert get_trigger_result(session, run_id) is None
