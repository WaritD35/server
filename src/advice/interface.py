"""Public published interface for the ``advice`` bounded context."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from advice.models import CarePlan
from advice.schemas import ActionType, CareAction, CarePlanRead


def _extract_actions(plan: CarePlan) -> list[CareAction]:
    """The plan's actions, from its ``care_plan_action`` rows (priority order)."""
    return [
        CareAction(
            id=a.action_id,
            priority=a.priority,
            action=a.action,
            label=a.label,
            type=(
                ActionType(a.action_type)
                if a.action_type in ActionType._value2member_map_
                else ActionType.OTHER
            ),
            approved_action_id=a.approved_action_id,
            check_slug=a.check_slug,
            urgency=a.urgency,
            expect_typical_hours=a.expect_typical_hours,
            expect_max_hours=a.expect_max_hours,
        )
        for a in plan.actions
    ]


def get_care_plan(session: Session, run_id: uuid.UUID) -> CarePlanRead | None:
    """Retrieve the CarePlan generated for a specific pipeline run."""
    stmt = select(CarePlan).where(CarePlan.run_id == run_id)
    plan = session.scalars(stmt).first()
    if not plan:
        return None
    return CarePlanRead(
        id=plan.id,
        care_plan_id=plan.care_plan_id,
        plant_id=plan.plant_id,
        run_id=plan.run_id,
        status_label=plan.status_label,
        assessment=plan.assessment,
        confidence=plan.confidence,
        actions=_extract_actions(plan),
        created_at=plan.created_at,
    )


def get_latest_care_plan_for_plant(session: Session, plant_id: uuid.UUID) -> CarePlanRead | None:
    """Retrieve the most recent CarePlan prescribed for a plant."""
    stmt = (
        select(CarePlan)
        .where(CarePlan.plant_id == plant_id)
        .order_by(CarePlan.created_at.desc())
        .limit(1)
    )
    plan = session.scalars(stmt).first()
    if not plan:
        return None
    return CarePlanRead(
        id=plan.id,
        care_plan_id=plan.care_plan_id,
        plant_id=plan.plant_id,
        run_id=plan.run_id,
        status_label=plan.status_label,
        assessment=plan.assessment,
        confidence=plan.confidence,
        actions=_extract_actions(plan),
        created_at=plan.created_at,
    )

