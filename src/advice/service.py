"""Service layer for the ``advice`` bounded context."""

import uuid

from sqlalchemy.orm import Session

from advice.models import CarePlan, CarePlanAction, Diagnosis
from assessment.interface import get_recent_observations, get_trigger_result
from assessment.schemas import TriggerDecision
from knowledge.interface import get_care_knowledge
from mlops.advice.runtime import AdviceInput, generate_advice
from registry.models import Plant


def generate_for_plant(
    session: Session,
    plant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> CarePlan | None:
    """Generate and persist a structured CarePlan if assessment requires care advice."""
    trigger = get_trigger_result(session, run_id)
    if trigger is None or trigger.decision != TriggerDecision.CARE_ADVICE_REQUIRED:
        # No care advice needed for healthy or low-confidence scans
        return None

    # Fetch plant profile from registry
    plant = session.get(Plant, plant_id)
    species_code = plant.species_code if plant and plant.species_code else "generic"

    # Fetch recent observations (last 5)
    recent_obs = get_recent_observations(session, plant_id, n=5)
    latest_obs = recent_obs[-1] if recent_obs else None

    # Retrieve botanical RAG knowledge from knowledge context
    knowledge_context = get_care_knowledge(session, species_code)

    symptoms_list = latest_obs.observations if latest_obs else []
    history_summary = [
        {
            "timestamp": str(o.timestamp),
            "health_status": o.health_status.value,
            "symptoms": o.observations,
        }
        for o in recent_obs[:-1]
    ]

    payload = AdviceInput(
        plant_id=str(plant_id),
        health_status=latest_obs.health_status.value if latest_obs else "unhealthy",
        symptoms=symptoms_list,
        observation_history=history_summary,
        knowledge_context=knowledge_context,
        trigger_reason=trigger.reasoning,
        confidence_agreement=trigger.confidence,
    )

    result = generate_advice(payload)

    # Persist CarePlan
    plan = CarePlan(
        care_plan_id=result.care_plan_id,
        plant_id=plant_id,
        run_id=run_id,
        status_label=result.status_label,
        assessment=result.assessment,
        confidence=result.confidence,
        actions_json=result.actions,
    )
    session.add(plan)
    session.flush()

    # Persist normalized CarePlanAction records
    for act in result.actions:
        action_item = CarePlanAction(
            care_plan_pk=plan.id,
            care_plan_id=plan.care_plan_id,
            action_id=act.get("id", f"act_{uuid.uuid4().hex[:8]}"),
            priority=act.get("priority", 1),
            action=act.get("action", ""),
            label=act.get("label", ""),
            action_type=act.get("type", "other"),
        )
        session.add(action_item)

    # Persist Diagnosis record
    diag = Diagnosis(
        plant_id=plant_id,
        run_id=run_id,
        diagnosis=result.assessment,
    )
    session.add(diag)

    session.commit()
    session.refresh(plan)
    return plan
