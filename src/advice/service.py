"""Service layer for the ``advice`` bounded context.

Care knowledge comes from ``knowledge``'s **approved** probe set only — the
checks assessment found firing and the approved actions behind them. Research
text is never read here.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from advice.models import CarePlan, CarePlanAction, Diagnosis
from assessment.interface import (
    get_observation_by_run,
    get_recent_observations,
    get_trigger_result,
)
from assessment.schemas import ObservationRead, TriggerDecision
from knowledge.interface import get_species_probes, search_approved_passages
from mlops.advice.runtime import AdviceInput, ApprovedAction, FiredCheck, generate_advice
from registry.interface import get_plant

logger = logging.getLogger(__name__)


def _fired_checks(observation: ObservationRead | None) -> list[FiredCheck]:
    """The approved checks that fired on this run's observation, in asked order."""
    if observation is None:
        return []
    return [
        FiredCheck(
            slug=r["slug"],
            care_need=str(r.get("care_need", "")),
            question=str(r.get("question", "")),
            evidence=str(r.get("evidence", "")),
        )
        for r in observation.probe_results
        if isinstance(r, dict) and r.get("fired") and isinstance(r.get("slug"), str)
    ]


def _approved_actions(
    session: Session, species_code: str | None, fired: list[FiredCheck]
) -> list[ApprovedAction]:
    """The approved actions a plan may use.

    Those of the fired checks, in the order the checks fired; when none fired
    (the trigger was a visual symptom), every approved action of the species, by
    check priority. Empty when the species has no approved probe set.
    """
    if species_code is None:
        return []
    bundle = get_species_probes(session, species_code)
    if bundle is None:
        return []
    by_slug = {p.slug: p for p in bundle.probes}
    if fired:
        probes = [by_slug[c.slug] for c in fired if c.slug in by_slug]
    else:
        probes = sorted(bundle.probes, key=lambda p: (not p.is_screening, p.priority))
    return [
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
        for p in probes
        for a in sorted(p.actions, key=lambda a: a.ordering)
    ]


def _passage_query(
    current: ObservationRead | None,
    trigger_reason: str | None,
    fired: list[FiredCheck],
) -> str:
    """Symptoms, trigger, and fired checks: the hybrid-search query."""
    symptoms = current.observations if current else []
    lines = [
        f"Symptoms: {symptoms}",
        f"Trigger: {trigger_reason or 'Health change'}",
    ]
    if fired:
        lines.append("Fired checks:")
        for check in fired:
            seen = f" Seen: {check.evidence}" if check.evidence else ""
            lines.append(f"- {check.slug}: {check.question}{seen}")
    else:
        lines.append("None of the approved checks fired.")
    return "\n".join(lines)


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

    fired = _fired_checks(current)
    approved = _approved_actions(session, species_code, fired)
    retrieved = ""
    if approved and species_code is not None:
        try:
            retrieved = search_approved_passages(
                session, species_code, _passage_query(current, trigger.reasoning, fired)
            )
        except Exception:
            logger.warning(
                "passage search failed for plant %s; assessment without passages",
                plant_id,
                exc_info=True,
            )

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
        retrieved_passages=retrieved,
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
        actions_json=result.actions,
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
