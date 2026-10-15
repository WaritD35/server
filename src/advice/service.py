"""Service layer for the ``advice`` bounded context.

Care knowledge comes from ``knowledge``'s **approved, structured** probe set
only, read through :func:`knowledge.interface.get_approved_checks` — just the
checks assessment found firing on the run, with their approved basis and
actions. Nothing is searched and no research text is read here.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from advice.models import CarePlan, CarePlanAction
from assessment.interface import (
    get_observation_by_run,
    get_recent_observations,
    get_trigger_result,
)
from assessment.schemas import ObservationRead, TriggerDecision
from knowledge.interface import get_approved_checks
from mlops.advice.runtime import AdviceInput, ApprovedAction, FiredCheck, generate_advice
from registry.interface import get_plant

logger = logging.getLogger(__name__)


def _fired_results(observation: ObservationRead | None) -> list[dict[str, Any]]:
    """The approved checks that fired on this run's observation, in asked order."""
    if observation is None:
        return []
    return [
        r
        for r in observation.probe_results
        if isinstance(r, dict) and r.get("fired") and isinstance(r.get("slug"), str)
    ]


def _needed_knowledge(
    session: Session, species_code: str | None, fired: list[dict[str, Any]]
) -> tuple[list[FiredCheck], list[ApprovedAction]]:
    """Only the approved knowledge this plan needs, as LLM input.

    With fired checks: just those checks (``knowledge.get_approved_checks`` with
    their slugs), each paired with what the VLM saw and its approved basis, and
    only their actions. With none (the trigger was a visual symptom): no check
    context, and every approved action of the species as candidates. Both empty
    when the species has no approved probe set.
    """
    if species_code is None:
        return [], []
    slugs = [r["slug"] for r in fired]
    checks = get_approved_checks(session, species_code, slugs or None)
    if checks is None:
        return [], []
    seen = {r["slug"]: str(r.get("evidence", "")) for r in fired}
    fired_checks = [
        FiredCheck(
            slug=p.slug,
            care_need=p.care_need,
            question=p.question,
            evidence=seen.get(p.slug, ""),
            basis=p.evidence_quote or "",
            not_this=p.not_this,
        )
        for p in checks.probes
        if p.slug in seen
    ]
    actions = [
        ApprovedAction(
            id=str(a.id),
            check_slug=p.slug,
            care_need=p.care_need,
            instruction=a.instruction,
            urgency=a.urgency,
            expect_typical_hours=a.expect_typical_hours,
            expect_max_hours=a.expect_max_hours,
            expected_signal=a.expected_signal,
        )
        for p in checks.probes
        for a in p.actions
    ]
    return fired_checks, actions


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

    plant = get_plant(session, plant_id)
    species_code = plant.species_code if plant is not None else None

    # This run's observation, plus the ones before it as history
    recent_obs = get_recent_observations(session, plant_id, n=5)
    current = get_observation_by_run(session, run_id) or (recent_obs[-1] if recent_obs else None)
    history = [o for o in recent_obs if current is None or o.id != current.id]

    fired, approved = _needed_knowledge(session, species_code, _fired_results(current))

    payload = AdviceInput(
        plant_id=str(plant_id),
        health_status=current.health_status.value if current else "unhealthy",
        symptoms=current.observations if current else [],
        observation_history=[
            {
                "timestamp": str(o.timestamp),
                "health_status": o.health_status.value,
                "symptoms": o.observations,
            }
            for o in history
        ],
        trigger_reason=trigger.reasoning,
        confidence_agreement=trigger.confidence,
        fired_checks=tuple(fired),
        approved_actions=tuple(approved),
        run_id=str(run_id),
    )

    result = generate_advice(payload)
    logger.info(
        "care plan for plant %s run %s: source=%s prompt=%s actions=%d",
        plant_id,
        run_id,
        result.source,
        result.prompt_ref,
        len(result.actions),
    )

    # Persist CarePlan
    plan = CarePlan(
        care_plan_id=result.care_plan_id,
        plant_id=plant_id,
        run_id=run_id,
        status_label=result.status_label,
        assessment=result.assessment,
        confidence=result.confidence,
    )
    session.add(plan)
    session.flush()

    # Persist normalized CarePlanAction records
    for act in result.actions:
        approved_id = act.get("approved_action_id")
        action_item = CarePlanAction(
            care_plan_pk=plan.id,
            care_plan_id=plan.care_plan_id,
            action_id=act.get("id", f"act_{uuid.uuid4().hex[:8]}"),
            priority=act.get("priority", 1),
            action=act.get("action", ""),
            label=act.get("label", ""),
            action_type=act.get("type", "other"),
            approved_action_id=uuid.UUID(approved_id) if approved_id else None,
            check_slug=act.get("check_slug"),
            urgency=act.get("urgency"),
            expect_typical_hours=act.get("expect_typical_hours"),
            expect_max_hours=act.get("expect_max_hours"),
        )
        session.add(action_item)

    session.commit()
    session.refresh(plan)
    return plan
