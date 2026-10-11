"""Service layer for ``companion`` — persona dialogue and message generation."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from assessment.interface import (
    get_milestones,
    get_recent_observations,
    update_companion_message,
)
from assessment.schemas import HealthStatus, TriggerDecision
from companion.models import MessageRecord
from companion.schemas import CompanionMessageRead
from companion.validator import validate_fact_preservation
from mlops.companion import generate_companion_message

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from advice.schemas import CarePlanRead
    from assessment.schemas import ObservationRead

logger = logging.getLogger(__name__)


def generate_steady_message(
    obs: ObservationRead | None = None,
    previous_obs: ObservationRead | None = None,
    plant_nickname: str = "your plant",
) -> str:
    """Generate static template message when plant is steady/improving (0 tokens)."""
    is_improving = (
        previous_obs is not None
        and previous_obs.health_status in (HealthStatus.UNHEALTHY, HealthStatus.POSSIBLY_UNHEALTHY)
        and obs is not None
        and (
            obs.health_status == HealthStatus.HEALTHY
            or (
                previous_obs.health_status == HealthStatus.UNHEALTHY
                and obs.health_status == HealthStatus.POSSIBLY_UNHEALTHY
            )
        )
    )

    if is_improving:
        return (
            "I'm feeling much better today and bouncing back! Thanks for taking good care of me."
        )
    return (
        "I'm feeling great and thriving today! Leaves are happy and soaking up the room. "
        "Thanks for checking in on me!"
    )


def generate_info_request_message(plant_nickname: str = "your plant") -> str:
    """Generate static template message when image clarity is insufficient (0 tokens)."""
    return (
        "Hmm, I couldn't get a clear look at my leaves in that photo — it might be a bit too "
        "blurry or dark. Could you snap another clear photo for me?"
    )


def format_fallback_template(
    care_plan: CarePlanRead,
    plant_nickname: str = "your plant",
) -> str:
    """Deterministic fallback format when LLM is unavailable or fails validation."""
    if not care_plan.actions:
        return f"{plant_nickname} is doing well. {care_plan.assessment}"

    action_lines = "\n".join(
        f"  • {item.action}"
        for item in sorted(care_plan.actions, key=lambda x: x.priority)
    )
    return (
        f"Update on {plant_nickname}:\n"
        f"{care_plan.assessment}\n\n"
        f"Recommended actions:\n"
        f"{action_lines}"
    )


def generate_for_plant(
    session: Session,
    plant_id: uuid.UUID,
    run_id: uuid.UUID,
    decision: str,
    care_plan: CarePlanRead | None = None,
    observation: ObservationRead | None = None,
    previous_observation: ObservationRead | None = None,
    plant_nickname: str = "your plant",
) -> CompanionMessageRead:
    """Evaluate pipeline state, generate appropriate companion message, and persist it."""
    if decision == TriggerDecision.NO_ACTION:
        message_text = generate_steady_message(
            observation, previous_observation, plant_nickname
        )
        source = "template_steady"
    elif decision == TriggerDecision.REQUEST_MORE_INFORMATION:
        message_text = generate_info_request_message(plant_nickname)
        source = "template_info_request"
    elif decision == TriggerDecision.CARE_ADVICE_REQUIRED:
        if care_plan is None:
            message_text = f"{plant_nickname} needs some attention, but no care plan was provided."
            source = "template_fallback"
        else:
            recent_obs = get_recent_observations(session, plant_id, n=7)
            milestones = get_milestones(session, plant_id)
            actions = [a.action for a in care_plan.actions]
            health_status = observation.health_status.value if observation else "unhealthy"

            llm_text = generate_companion_message(
                plant_nickname=plant_nickname,
                assessment=care_plan.assessment,
                actions=actions,
                health_status=health_status,
                recent_observations=recent_obs,
                milestones=milestones,
            )

            if llm_text:
                is_valid, missing = validate_fact_preservation(care_plan, llm_text)
                if is_valid:
                    message_text = llm_text
                    source = "llm"
                else:
                    logger.warning(
                        "Companion LLM dropped required actions %s. Falling back to template.",
                        missing,
                    )
                    message_text = format_fallback_template(care_plan, plant_nickname)
                    source = "template_fallback"
            else:
                message_text = format_fallback_template(care_plan, plant_nickname)
                source = "template_fallback"
    else:
        message_text = f"Status update for {plant_nickname}."
        source = "template_steady"

    record = MessageRecord(
        plant_id=plant_id,
        run_id=run_id,
        decision=str(decision),
        message=message_text,
        source=source,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    # Backfill companion message into the observation record
    try:
        update_companion_message(session, run_id, message_text)
    except Exception as e:
        logger.warning("Could not backfill companion message to observation: %s", e)

    return CompanionMessageRead(
        id=record.id,
        plant_id=record.plant_id,
        run_id=record.run_id,
        decision=record.decision,
        message=record.message,
        source=record.source,
        created_at=record.created_at,
    )
