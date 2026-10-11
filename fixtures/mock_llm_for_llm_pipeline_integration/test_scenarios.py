#!/usr/bin/env python3
"""Interactive Scenario & LLM Integration Test Runner for Pet Plant Server.

Runs each plant health scenario through the complete Orchestrator Pipeline:
  Capture -> Assessment -> Advice (LLM Diagnosis & 3NF Actions) -> Companion (LLM Persona Dialogue)

Supports:
  - Offline Mock Mode (default): Uses canned LLM responses from fixtures/mock_llm/responses/
  - Live LLM Mode (--live-llm): Uses running Ollama or OpenAI configured in .env
  - Frontend Response Output (--fe): Prints exact FE response JSON envelopes matching poc-ai-agent

CLI to test:
  - uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py --fe
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Ensure src/ and current dir are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
FIXTURES_DIR = Path(__file__).resolve().parent

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from action.db import Base as ActionBase
from advice.db import Base as AdviceBase
from advice.models import CarePlan, CarePlanAction
from assessment.db import Base as AssessmentBase
from assessment.models import Observation, PlantMilestone, TriggerResultRecord
from assessment.schemas import HealthStatus
from companion.db import Base as CompanionBase
from companion.models import MessageRecord
from core.db import Base as AuthBase
from mock_model import MockChatModel
from knowledge.db import Base as KnowledgeBase
from orchestrator.db import Base as OrchestratorBase
from orchestrator.models import PipelineRun
from orchestrator.stages import STAGES
from orchestrator.worker import execute_run
from registry.db import Base as RegistryBase
from registry.models import Plant

ALL_BASES = (
    AuthBase,
    RegistryBase,
    KnowledgeBase,
    OrchestratorBase,
    AssessmentBase,
    AdviceBase,
    ActionBase,
    CompanionBase,
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "no_change": {
        "file": "scenarios/no_change.json",
        "expected": "expected_outputs/scenario_no_change.json",
        "mock_advice": [],
        "mock_companion": "responses/companion/healthy_steady.json",
    },
    "new_symptom": {
        "file": "scenarios/new_symptom.json",
        "expected": "expected_outputs/scenario_new_symptom.json",
        "mock_advice": ["responses/advice/leaf_yellowing_mild.json"],
        "mock_companion": "responses/companion/care_advice_message.json",
    },
    "severity_increase": {
        "file": "scenarios/severity_increase.json",
        "expected": "expected_outputs/scenario_severity_increase.json",
        "mock_advice": [
            "responses/advice/leaf_yellowing_mild.json",
            "responses/advice/leaf_yellowing_severe.json",
        ],
        "mock_companion": "responses/companion/crisis_alert.json",
    },
    "improvement": {
        "file": "scenarios/improvement.json",
        "expected": "expected_outputs/scenario_improvement.json",
        "mock_advice": ["responses/advice/leaf_yellowing_severe.json"],
        "mock_companion": "responses/companion/improvement_recovery.json",
    },
    "health_status_change": {
        "file": "scenarios/health_status_change.json",
        "expected": "expected_outputs/scenario_health_status_change.json",
        "mock_advice": ["responses/advice/overwatering_stress.json"],
        "mock_companion": "responses/companion/care_advice_message.json",
    },
    "multi_day": {
        "file": "scenarios/milestone_lifecycle.json",
        "expected": "expected_outputs/scenario_multi_day.json",
        "mock_advice": [
            "responses/advice/fungal_infection.json",
            "responses/advice/multi_symptom.json",
        ],
        "mock_companion": "responses/companion/improvement_recovery.json",
    },
}


def load_fixture_json(rel_path: str) -> dict[str, Any] | None:
    full_path = FIXTURES_DIR / rel_path
    if not full_path.exists():
        return None
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_session_factory() -> sessionmaker[Session]:
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
    for base in ALL_BASES:
        base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def build_frontend_response(
    plant: Plant,
    day_count: int,
    timestamp: datetime,
    raw_status: str,
    decision: str,
    companion_message: str | None,
    care_plan: CarePlan | None,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mirror PipelineStepResult.to_frontend_dict() from poc-ai-agent."""
    return {
        "plant_id": str(plant.id),
        "name": plant.name,
        "species": plant.species_code or "Unknown species",
        "dayCount": day_count,
        "timestamp": timestamp.isoformat(),
        "wateredTimestamp": None,
        "level": 1,
        "xpRatio": 0.0,
        "health_status": raw_status,
        "decision": decision,
        "companion_message": companion_message,
        "care_plan": (
            {
                "id": care_plan.care_plan_id,
                "status_label": care_plan.status_label,
                "assessment": care_plan.assessment,
                "actions": sorted(actions, key=lambda x: x.get("priority", 1)),
            }
            if care_plan
            else None
        ),
    }


def build_response_envelope(
    frontend_data: dict[str, Any], message: str | None = None
) -> dict[str, Any]:
    """Mirror PipelineStepResult.to_response_envelope() from poc-ai-agent."""
    return {
        "success": True,
        "data": frontend_data,
        "message": message,
    }


def run_scenario(
    name: str,
    spec: dict[str, Any],
    live_llm: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    scenario_data = load_fixture_json(spec["file"])
    expected_data = load_fixture_json(spec["expected"])

    if scenario_data is None or expected_data is None:
        raise FileNotFoundError(f"Missing fixture file for scenario: {name}")

    observations_raw = scenario_data["observations"]
    expected_decisions = expected_data.get("expected_decisions", [])

    session_factory = build_session_factory()

    plant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    with session_factory() as session:
        # Register test plant
        plant = Plant(
            id=plant_id,
            owner_id=user_id,
            name="Monty Monstera",
            species_code="monstera_deliciosa",
        )
        session.add(plant)
        session.commit()

    # Setup mock LLM responses if in offline mock mode
    canned_responses: list[Any] = []
    if not live_llm:
        for adv_path in spec.get("mock_advice", []):
            data = load_fixture_json(adv_path)
            if data is not None:
                canned_responses.append(json.dumps(data))
        comp_data = load_fixture_json(spec.get("mock_companion", ""))
        if comp_data is not None and "message" in comp_data:
            for _ in range(len(observations_raw)):
                canned_responses.append(comp_data["message"])

    mock_llm = MockChatModel(responses=canned_responses)

    step_results: list[dict[str, Any]] = []

    for idx, obs_raw in enumerate(observations_raw, 1):
        run_id = uuid.uuid4()
        now = datetime.now(UTC)

        raw_status = obs_raw.get("health_status", "healthy")
        raw_conf = obs_raw.get("consensus", {}).get("model_stated_average", 0.95)
        raw_symptoms = obs_raw.get("observations", [])
        raw_consensus = obs_raw.get("consensus", {})
        raw_images = obs_raw.get("image_refs", [])

        with session_factory() as session:
            # 1. Ingest observation
            obs = Observation(
                id=uuid.uuid4(),
                plant_id=plant_id,
                run_id=run_id,
                timestamp=now,
                health_status=HealthStatus(raw_status).value,
                confidence=raw_conf,
                observations_json=raw_symptoms,
                consensus_json=raw_consensus,
                image_refs_json=raw_images,
                created_at=now,
            )
            session.add(obs)

            # 2. Register pipeline run
            run = PipelineRun(
                id=run_id,
                plant_id=plant_id,
                scheduled_for=now,
                status="running",
                current_stage="capture",
            )
            session.add(run)
            session.commit()

            # 3. Execute run across stages
            with patch("orchestrator.stages.session_factory", session_factory):
                if not live_llm:
                    with patch("mlops.factory.get_llm", return_value=mock_llm):
                        status = execute_run(session, run, stages=STAGES)
                else:
                    status = execute_run(session, run, stages=STAGES)

            # 4. Read back database entities
            tr = session.scalars(
                select(TriggerResultRecord).where(TriggerResultRecord.run_id == run_id)
            ).first()

            cp = session.scalars(
                select(CarePlan).where(CarePlan.run_id == run_id)
            ).first()

            actions: list[dict[str, Any]] = []
            if cp:
                action_rows = session.scalars(
                    select(CarePlanAction)
                    .where(CarePlanAction.care_plan_id == cp.care_plan_id)
                    .order_by(CarePlanAction.priority.asc())
                ).all()
                for a in action_rows:
                    actions.append({
                        "id": a.action_id,
                        "priority": a.priority,
                        "label": a.label,
                        "action": a.action,
                        "type": a.action_type,
                    })

            msg_row = session.scalars(
                select(MessageRecord).where(MessageRecord.run_id == run_id)
            ).first()

            milestones_rows = session.scalars(
                select(PlantMilestone).where(PlantMilestone.plant_id == plant_id)
            ).all()

            decision_str = tr.decision if tr else "UNKNOWN"
            comp_msg_str = msg_row.message if msg_row else None

            # Build FE Response Payload (identical to poc-ai-agent)
            fe_dict = build_frontend_response(
                plant=plant,
                day_count=idx,
                timestamp=now,
                raw_status=raw_status,
                decision=decision_str,
                companion_message=comp_msg_str,
                care_plan=cp,
                actions=actions,
            )
            fe_envelope = build_response_envelope(fe_dict)

            step_results.append({
                "day": idx,
                "run_id": str(run_id),
                "pipeline_status": status,
                "decision": decision_str,
                "reasoning": tr.reasoning if tr else "",
                "care_plan": {
                    "id": cp.care_plan_id,
                    "status_label": cp.status_label,
                    "assessment": cp.assessment,
                    "confidence": cp.confidence,
                    "actions": actions,
                }
                if cp
                else None,
                "companion_message": comp_msg_str,
                "companion_source": msg_row.source if msg_row else None,
                "milestones_count": len(milestones_rows),
                "frontend_response": fe_dict,
                "response_envelope": fe_envelope,
            })

    # Validate assertions against expected outputs
    actual_decisions = [s["decision"] for s in step_results]
    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "step_count",
        len(step_results) == len(observations_raw),
        f"Expected {len(observations_raw)} steps, got {len(step_results)}",
    ))

    checks.append((
        "decision_sequence",
        actual_decisions == expected_decisions,
        f"Expected {expected_decisions}, got {actual_decisions}",
    ))

    has_advice = any(d == "CARE_ADVICE_REQUIRED" for d in actual_decisions)
    has_plan = any(s["care_plan"] is not None for s in step_results)

    if has_advice:
        checks.append((
            "care_plan_persisted",
            has_plan,
            "Care plan must be persisted when CARE_ADVICE_REQUIRED",
        ))
        for s in step_results:
            if s["care_plan"]:
                acts = s["care_plan"]["actions"]
                checks.append((
                    f"actions_persisted_day_{s['day']}",
                    len(acts) > 0,
                    f"Day {s['day']} must have 3NF care actions in advice.care_plan_action",
                ))
    else:
        checks.append((
            "no_unneeded_plan",
            not has_plan,
            "Healthy/no-action run should not persist a care plan",
        ))

    all_have_companion = all(s["companion_message"] is not None for s in step_results)
    checks.append((
        "companion_dialogue_present",
        all_have_companion,
        "Every run must store a companion dialogue message",
    ))

    # Validate frontend response envelope consistency
    checks.append((
        "frontend_payload_valid",
        all(
            bool(s["frontend_response"]["plant_id"] and s["frontend_response"]["decision"])
            for s in step_results
        ),
        "Every step must generate a valid frontend response envelope",
    ))

    passed = all(ok for _, ok, _ in checks)

    return {
        "scenario": name,
        "description": expected_data.get("description", ""),
        "passed": passed,
        "actual_decisions": actual_decisions,
        "expected_decisions": expected_decisions,
        "checks": checks,
        "steps": step_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run LLM Integration Scenario Tests for Pet Plant Server"
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        help="Run only a specific scenario",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use live Ollama/OpenAI instead of offline mock fixtures",
    )
    parser.add_argument(
        "--fe",
        action="store_true",
        help="Print exact Frontend Response Envelopes (same format as poc-ai-agent)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed per-step diagnostic, actions, and companion output",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Export results summary to JSON file",
    )
    args = parser.parse_args()

    scenarios_to_run = (
        {args.scenario: SCENARIOS[args.scenario]}
        if args.scenario
        else SCENARIOS
    )

    mode_label = "LIVE LLM (Ollama/OpenAI)" if args.live_llm else "OFFLINE MOCK FIXTURES"

    print("=" * 72)
    print(" PET PLANT SERVER — LLM INTEGRATION SCENARIO VERIFICATION ")
    print(f" Mode: {mode_label}")
    print("=" * 72)

    results: list[dict[str, Any]] = []
    all_passed = True

    for name, spec in scenarios_to_run.items():
        res = run_scenario(name, spec, live_llm=args.live_llm, verbose=args.verbose)
        results.append(res)
        status_icon = "✓ PASS" if res["passed"] else "✗ FAIL"
        if not res["passed"]:
            all_passed = False

        print(f"\n[{status_icon}] Scenario: {name}")
        print(f"       Description: {res['description']}")
        print(f"       Decisions:   {' -> '.join(res['actual_decisions'])}")

        if args.verbose or not res["passed"]:
            for check_name, ok, msg in res["checks"]:
                mark = "  ✓" if ok else "  ✗"
                print(f"    {mark} {check_name}: {msg}")

        # If --fe or --verbose, print the frontend response envelopes
        if args.fe or args.verbose:
            for step in res["steps"]:
                print(f"\n      ╔══ Day {step['day']} Frontend Response Envelope (Client Payload) ══╗")
                formatted_json = json.dumps(step["response_envelope"], indent=2)
                for line in formatted_json.splitlines():
                    print(f"      ║ {line}")
                print(f"      ╚{'═' * 66}╝")

    print("\n" + "=" * 72)
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    print(f" SUMMARY: {passed_count}/{total} Scenarios Passed")
    print("=" * 72)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results exported to {args.output}")

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
