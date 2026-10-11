"""``/capture`` router — take one webcam capture by hand, or list recent frames.

Superuser only: a capture touches hardware. Captures normally run as the
orchestrator's ``capture`` stage; ``run_id`` is ``NULL`` on rows stored from
here. Image bytes are never returned. Mounted by ``main_web``.
"""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from capture import service
from capture.models import ProcessedFrame
from capture.schemas import CaptureRunRead, ProcessedFrameRead
from core.db import get_session
from core.users import CurrentSuperuser
from registry import get_plant

router = APIRouter(prefix="/capture", tags=["capture"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post(
    "/plants/{plant_id}/run",
    response_model=CaptureRunRead,
    summary="Capture one frame for a plant now, outside the schedule",
    description=(
        "404 for an unknown or archived plant; 409 when the plant has no device, its "
        "device is not in CAPTURE_WEBCAM_MAP, or the webcam is busy; 503 when the "
        "webcam or object storage fails. A frame that fails the quality gate is "
        "stored and answers 200 with `quality_passed: false`."
    ),
)
def run_capture(
    plant_id: uuid.UUID, session: SessionDep, _user: CurrentSuperuser
) -> CaptureRunRead:
    plant = get_plant(session, plant_id)
    if plant is None or not plant.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    try:
        result = service.capture_for_plant(session, plant_id)
    except (service.CaptureUnavailable, service.CaptureBusy) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except service.CaptureDeviceError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None

    capture, frame = result.capture, result.processed_frame
    return CaptureRunRead(
        capture_id=capture.id,
        plant_id=capture.plant_id,
        captured_at=capture.captured_at,
        quality_passed=capture.quality_passed,
        quality_reason=capture.quality_reason,
        brightness=capture.brightness,
        blur_score=capture.blur_score,
        processed_frame=None if frame is None else ProcessedFrameRead.model_validate(frame),
    )


@router.get(
    "/plants/{plant_id}/frames",
    response_model=list[ProcessedFrameRead],
    summary="A plant's processed frames, newest first",
)
def list_frames(
    plant_id: uuid.UUID,
    session: SessionDep,
    _user: CurrentSuperuser,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> Sequence[ProcessedFrame]:
    if get_plant(session, plant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    return service.list_frames(session, plant_id, limit)
