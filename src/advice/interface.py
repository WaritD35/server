"""Public published interface for the ``advice`` bounded context."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from advice.models import CarePlan, CarePlanAction
from advice.schemas import ActionType, CareAction, CarePlanActionRead, CarePlanRead


def _extract_actions(plan: CarePlan) -> list[CareAction]:
    """Extract actions prioritizing normalized CarePlanAction table rows."""
    if plan.actions:
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
            )
            for a in plan.actions
        ]

    # Fallback to legacy actions_json if normalized table has no entries
    return [
        CareAction.model_validate(act)
        for act in (plan.actions_json if isinstance(plan.actions_json, list) else [])
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


def get_care_plan_actions(session: Session, care_plan_id: str) -> list[CarePlanActionRead]:
    """Retrieve normalized individual care action rows for a given care plan."""
    stmt = (
        select(CarePlanAction)
        .where(CarePlanAction.care_plan_id == care_plan_id)
        .order_by(CarePlanAction.priority.asc())
    )
    rows = session.scalars(stmt).all()
    return [
        CarePlanActionRead(
            id=r.id,
            care_plan_pk=r.care_plan_pk,
            care_plan_id=r.care_plan_id,
            action_id=r.action_id,
            priority=r.priority,
            action=r.action,
            label=r.label,
            action_type=r.action_type,
            created_at=r.created_at,
        )
        for r in rows
    ]
