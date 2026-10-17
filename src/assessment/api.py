"""``/assessment`` router — run the VLM probe by hand on the latest frame pair.

Superuser only: a run costs VLM tokens and writes a real observation that
counts in the plant's history. Assessments normally run as the orchestrator's
``assessment`` stage. Mounted by ``main_web``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from assessment import service
from assessment.models import Observation
from assessment.schemas import HealthStatus, ObservationRead
from capture.interface import CaptureDeviceError
from core.db import get_session
from core.users import CurrentSuperuser
from mlops.assessment import AssessmentRunError, AssessmentUnavailableError
from registry import get_plant

router = APIRouter(prefix="/assessment", tags=["assessment"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post(
    "/plants/{plant_id}/run",
    response_model=ObservationRead,
    summary="Assess a plant's latest two frames now, outside the schedule",
    description=(
        "404 for an unknown or archived plant; 409 when species is unset or fewer than two "
        "processed frames exist; 502 when most VLM runs are invalid; 503 when "
        "Langfuse, the prompt config, the VLM or object storage is unavailable. "
        "Does not run the rules engine. The stored observation counts in history."
    ),
)
def run_assessment(
    plant_id: uuid.UUID, session: SessionDep, _user: CurrentSuperuser
) -> ObservationRead:
    plant = get_plant(session, plant_id)
    if plant is None or not plant.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    try:
        observation = service.assess_frames(
            session, plant_id, uuid.uuid4(), frames_run_id=None
        )
    except service.AssessmentSkipped as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except AssessmentRunError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    except (AssessmentUnavailableError, CaptureDeviceError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None

    return _observation_read(observation)


def _observation_read(row: Observation) -> ObservationRead:
    return ObservationRead(
        id=row.id,
        plant_id=row.plant_id,
        run_id=row.run_id,
        timestamp=row.timestamp,
        health_status=HealthStatus(row.health_status),
        confidence=row.confidence,
        observations=row.observations_json if isinstance(row.observations_json, list) else [],
        consensus=row.consensus_json,
        image_refs=row.image_refs_json,
        probe_set_id=row.probe_set_id,
        probe_results=row.probe_results_json if isinstance(row.probe_results_json, list) else [],
        description=row.description,
        companion_message=row.companion_message,
        created_at=row.created_at,
    )
