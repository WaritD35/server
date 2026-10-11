"""The pipeline stages, in order, and the functions that call into each context.

Each stage function is the **only** place ``orchestrator`` touches that
context. It receives the identifiers of the work (:class:`StageInput`) — never
another stage's output: every context stores its result in its own schema, and
the next one reads it from there.

A stage function **returns** when its work is stored, and may **raise**
:class:`StageSkipped` when there is nothing to do (e.g. no new frames), which
ends the run without running the later stages.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from advice.interface import get_care_plan
from advice.service import generate_for_plant as generate_advice_for_plant
from assessment.interface import get_previous_observation, get_trigger_result
from assessment.models import Observation
from assessment.schemas import HealthStatus, ObservationRead, TriggerDecision
from assessment.service import evaluate_observation
from companion.service import generate_for_plant as generate_companion_message
from core.db import session_factory
from registry.interface import get_plant

logger = logging.getLogger(__name__)


class StageSkipped(Exception):  # noqa: N818 - reads as an outcome, not an error
    """Raised by a stage with nothing to do; the message is kept as the reason."""


@dataclass(frozen=True)
class StageInput:
    run_id: uuid.UUID
    plant_id: uuid.UUID
    #: The schedule slot (UTC) the run belongs to.
    scheduled_for: datetime


@dataclass(frozen=True)
class Stage:
    name: str
    fn: Callable[[StageInput], None]


def run_capture(payload: StageInput) -> None:
    """Preprocess the plant's latest capture batch (quality check, segment, align)."""
    logger.info("capture stage plant=%s run=%s", payload.plant_id, payload.run_id)


def run_assessment(payload: StageInput) -> None:
    """Run deterministic rule evaluation and milestone detection for the run."""
    with session_factory() as session:
        try:
            evaluate_observation(session, payload.plant_id, payload.run_id)
        except ValueError as err:
            logger.info("Assessment stage skipped: %s", err)
            raise StageSkipped(str(err)) from err


def run_advice(payload: StageInput) -> None:
    """Turn stored assessment verdicts into a diagnosis and ranked care plan."""
    with session_factory() as session:
        # Invariant: Does not raise StageSkipped so companion can still execute
        generate_advice_for_plant(session, payload.plant_id, payload.run_id)


def run_companion(payload: StageInput) -> None:
    """Update companion dialogue message based on assessment and advice results."""
    with session_factory() as session:
        trig = get_trigger_result(session, payload.run_id)
        decision = trig.decision.value if trig else TriggerDecision.NO_ACTION.value

        stmt = select(Observation).where(Observation.run_id == payload.run_id)
        obs_row = session.scalars(stmt).first()
        obs_read = (
            ObservationRead(
                id=obs_row.id,
                plant_id=obs_row.plant_id,
                run_id=obs_row.run_id,
                timestamp=obs_row.timestamp,
                health_status=HealthStatus(obs_row.health_status),
                confidence=obs_row.confidence,
                observations=(
                    obs_row.observations_json
                    if isinstance(obs_row.observations_json, list)
                    else []
                ),
                consensus=obs_row.consensus_json,
                image_refs=obs_row.image_refs_json,
                description=obs_row.description,
                companion_message=obs_row.companion_message,
                created_at=obs_row.created_at,
            )
            if obs_row
            else None
        )

        prev_read = get_previous_observation(session, payload.plant_id)
        care_plan = get_care_plan(session, payload.run_id)
        plant_meta = get_plant(session, payload.plant_id)
        nickname = plant_meta.name if plant_meta else "your plant"

        generate_companion_message(
            session=session,
            plant_id=payload.plant_id,
            run_id=payload.run_id,
            decision=decision,
            care_plan=care_plan,
            observation=obs_read,
            previous_observation=prev_read,
            plant_nickname=nickname,
        )


#: Fixed by the architecture: capture → assessment → advice → companion.
STAGES: tuple[Stage, ...] = (
    Stage("capture", run_capture),
    Stage("assessment", run_assessment),
    Stage("advice", run_advice),
    Stage("companion", run_companion),
)
