"""``/capture`` router — photos uploaded by planter devices, and recent frames.

- ``POST /capture/devices/me/frames`` — a paired planter uploads a JPEG of the
  plant bound to it (``registry``). The normal path.
- ``POST /capture/plants/{plant_id}/frames`` — a superuser uploads a JPEG for a
  plant by hand (testing without a device).
- ``GET /capture/plants/{plant_id}/frames`` — a superuser lists recent frames.

An upload is quality-checked and stored at once; the answer says whether it
passed and, if not, why, so the device can retake it. It is tied to a pipeline
run only when the capture stage claims it. Image bytes are never returned.
Mounted by ``main_web``.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from capture import service
from capture.models import ProcessedFrame
from capture.schemas import CaptureRead, ProcessedFrameRead
from core.db import get_session
from core.devices import CurrentDevice
from core.users import CurrentSuperuser
from registry import get_plant, get_plant_by_device

router = APIRouter(prefix="/capture", tags=["capture"])

SessionDep = Annotated[Session, Depends(get_session)]
ImageFile = Annotated[UploadFile, File(description="The photo, as a JPEG")]
CapturedAt = Annotated[
    datetime | None,
    Form(description="When the photo was taken (device clock); defaults to receipt time"),
]

_UPLOAD_ERRORS = (
    "422 when the file is not a JPEG or captured_at is in the future; 413 when it is "
    "larger than 10 MB; 503 when object storage fails. A photo that fails the quality "
    "gate is stored and answers 201 with `quality_passed: false` and the reason."
)


async def _read_upload(image: UploadFile) -> bytes:
    # Read one byte past the limit so an oversized upload is caught without
    # holding more than that in memory.
    data = await image.read(service.MAX_UPLOAD_BYTES + 1)
    if len(data) > service.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Upload exceeds {service.MAX_UPLOAD_BYTES} bytes",
        )
    return data


def _ingest(
    session: Session,
    plant_id: uuid.UUID,
    *,
    device_id: str,
    data: bytes,
    captured_at: datetime | None,
) -> CaptureRead:
    try:
        result = service.ingest_frame(
            session, plant_id, device_id=device_id, jpeg=data, captured_at=captured_at
        )
    except service.InvalidUpload as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except service.CaptureDeviceError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None

    capture, frame = result.capture, result.processed_frame
    return CaptureRead(
        capture_id=capture.id,
        plant_id=capture.plant_id,
        captured_at=capture.captured_at,
        quality_passed=capture.quality_passed,
        quality_reason=capture.quality_reason,
        brightness=capture.brightness,
        blur_score=capture.blur_score,
        processed_frame=None if frame is None else ProcessedFrameRead.model_validate(frame),
    )


@router.post(
    "/devices/me/frames",
    response_model=CaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Device: upload a photo of the plant I photograph",
    description="404 when no live plant is bound to this device. " + _UPLOAD_ERRORS,
)
async def upload_device_frame(
    image: ImageFile,
    session: SessionDep,
    device: CurrentDevice,
    captured_at: CapturedAt = None,
) -> CaptureRead:
    plant = get_plant_by_device(session, device.physical_id)
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plant bound to this device")
    data = await _read_upload(image)
    return _ingest(
        session, plant.id, device_id=device.physical_id, data=data, captured_at=captured_at
    )


@router.post(
    "/plants/{plant_id}/frames",
    response_model=CaptureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: upload a photo for a plant by hand",
    description="404 for an unknown or archived plant. " + _UPLOAD_ERRORS,
)
async def upload_plant_frame(
    plant_id: uuid.UUID,
    image: ImageFile,
    session: SessionDep,
    _user: CurrentSuperuser,
    captured_at: CapturedAt = None,
) -> CaptureRead:
    plant = get_plant(session, plant_id)
    if plant is None or not plant.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    data = await _read_upload(image)
    return _ingest(
        session,
        plant_id,
        device_id=plant.device_id or "manual-upload",
        data=data,
        captured_at=captured_at,
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
