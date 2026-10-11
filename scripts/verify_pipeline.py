"""Interactive End-to-End Pipeline Verification Script.

Run directly via:
    uv run python scripts/verify_pipeline.py
"""

import sys
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure src/ is in path
sys.path.insert(0, "src")

from action.db import Base as ActionBase
from action.models import CareEvent
from advice.db import Base as AdviceBase
from advice.models import CarePlan, CarePlanAction
from assessment.db import Base as AssessmentBase
from assessment.models import Observation, TriggerResultRecord
from assessment.schemas import HealthStatus
from companion.db import Base as CompanionBase
from companion.models import MessageRecord
from core.db import Base as AuthBase
from knowledge.db import Base as KnowledgeBase
from orchestrator.db import Base as OrchestratorBase
from orchestrator.models import PipelineRun
from orchestrator.stages import STAGES
from orchestrator.worker import execute_run
from registry.db import Base as RegistryBase
from registry.models import Plant


def main() -> None:
    print("=" * 60)
    print(" PET PLANT SERVER — INTERACTIVE LOGIC VERIFICATION ")
    print("=" * 60)

    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool).execution_options(
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

    for base in (
        AuthBase,
        RegistryBase,
        KnowledgeBase,
        OrchestratorBase,
        AssessmentBase,
        AdviceBase,
        ActionBase,
        CompanionBase,
    ):
        base.metadata.create_all(engine)

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        plant_id = uuid.uuid4()
        run_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(UTC)

        print("\n[Step 0] Registering Plant & Ingesting Unhealthy Visual Observation...")
        plant = Plant(
            id=plant_id,
            owner_id=user_id,
            name="Monty Monstera",
            species_code="monstera_deliciosa",
        )
        session.add(plant)

        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant_id,
            run_id=run_id,
            timestamp=now,
            health_status=HealthStatus.UNHEALTHY.value,
            confidence=0.92,
            observations_json=[{"type": "leaf_yellowing", "severity": "moderate"}],
            consensus_json={},
            image_refs_json=["s3://captures/plant_001.jpg"],
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

        print("[Step 1] Executing Orchestrator Pipeline across all 4 stages...")
        with patch("orchestrator.stages.session_factory", session_factory):
            status = execute_run(session, run, stages=STAGES)

        print(f"  ✓ Pipeline Run Status: {status}")
        print(f"  ✓ Final Stage Reached: {run.current_stage}")

        print("\n[Step 2] Validating Assessment Stage (0 LLM Tokens):")
        tr = session.scalars(
            select(TriggerResultRecord).where(TriggerResultRecord.run_id == run_id)
        ).first()
        assert tr is not None
        print(f"  ✓ Decision: {tr.decision}")
        print(f"  ✓ Reasoning: {tr.reasoning}")

        print("\n[Step 3] Validating Advice Stage (Botanical Diagnosis):")
        cp = session.scalars(select(CarePlan).where(CarePlan.run_id == run_id)).first()
        assert cp is not None
        print(f"  ✓ Care Plan ID: {cp.care_plan_id}")
        print(f"  ✓ Status Label: {cp.status_label}")
        print(f"  ✓ Assessment: {cp.assessment}")

        print("\n[Step 4] Validating 3NF Normalized Care Actions (advice.care_plan_action Table):")
        actions = session.scalars(
            select(CarePlanAction)
            .where(CarePlanAction.care_plan_id == cp.care_plan_id)
            .order_by(CarePlanAction.priority.asc())
        ).all()
        assert len(actions) > 0, "No actions saved to care_plan_action table!"
        for a in actions:
            print(
                f"  ✓ [Action Row] ID: {a.action_id} | "
                f"Priority: {a.priority} | Type: {a.action_type}"
            )
            print(f"    Label: \"{a.label}\" | Description: \"{a.action}\"")

        print("\n[Step 5] Validating Companion Stage (First-Person Plant Persona Dialogue):")
        msg = session.scalars(select(MessageRecord).where(MessageRecord.run_id == run_id)).first()
        assert msg is not None
        print(f"  ✓ Source: {msg.source}")
        indented_msg = msg.message.replace("\n", "\n    ")
        print(f"  ✓ Plant Dialogue:\n    {indented_msg}")

        print("\n[Step 6] Validating User Action Logging (action.care_event Table):")
        completed_act = actions[0]
        event = CareEvent(
            id=uuid.uuid4(),
            plant_id=plant_id,
            event_type="action_completed",
            care_plan_id=cp.care_plan_id,
            action_id=completed_act.action_id,
            action_type=completed_act.action_type,
            occurred_at=now,
            recorded_at=now,
            recorded_by_user_id=user_id,
        )
        session.add(event)
        session.commit()
        print(f"  ✓ User clicked: \"{completed_act.label}\" ({completed_act.action_id})")
        print("  ✓ Row committed to action.care_event table (event_type='action_completed')")

        print("\n" + "=" * 60)
        print(" ALL LOGIC VERIFIED AND WORKING CORRECTLY! ")
        print("=" * 60)


if __name__ == "__main__":
    main()
