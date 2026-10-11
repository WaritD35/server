"""Webcam capture and frame queries for the ``capture`` context.

No HTTP concerns here. A capture grabs one frame from the webcam mapped to the
plant's ``device_id`` (``CAPTURE_WEBCAM_MAP``), quality-checks it and, if it
passes, crops the plant and records which earlier frame it is aligned to. Image
bytes go to MinIO (raw → ``captures``, processed → ``frames-derived``); nothing
is written to local disk. Object keys are generated here, never taken from a
caller.
"""

import io
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from minio import Minio
from minio.error import MinioException
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib3.exceptions import HTTPError

from capture.camera.config import CameraConfig, parse_webcam_map
from capture.camera.webcam import WebcamError, capture_once
from capture.db import utcnow
from capture.models import Capture, ProcessedFrame
from capture.vision.codec import encode_jpeg
from capture.vision.preprocess import PreprocessConfig, PreprocessError, preprocess_image
from capture.vision.quality import QualityConfig, assess_image_quality
from core.config import get_settings
from core.storage import bucket_names, get_minio_client
from registry.interface import get_plant

_STORAGE_ERRORS = (MinioException, HTTPError)


class CaptureUnavailable(Exception):  # noqa: N818 - reads as an outcome, not an error
    """Raised when the plant cannot be captured here (no device, or device unmapped)."""


class CaptureBusy(Exception):  # noqa: N818 - reads as an outcome, not an error
    """Raised when another capture already holds the plant's webcam."""


class CaptureDeviceError(RuntimeError):
    """Raised when the webcam fails to open/read or object storage fails."""


@dataclass(frozen=True)
class CaptureResult:
    capture: Capture
    #: ``None`` when the frame failed the quality gate.
    processed_frame: ProcessedFrame | None

    @property
    def quality_passed(self) -> bool:
        return self.capture.quality_passed

    @property
    def quality_reason(self) -> str | None:
        return self.capture.quality_reason


_device_locks: dict[str, threading.Lock] = {}
_device_locks_guard = threading.Lock()


@contextmanager
def _device_lock(device_id: str) -> Iterator[None]:
    with _device_locks_guard:
        lock = _device_locks.setdefault(device_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise CaptureBusy(f"Webcam for device {device_id!r} is busy")
    try:
        yield
    finally:
        lock.release()


def _day_prefix(plant_id: uuid.UUID, at: datetime) -> str:
    return f"{plant_id}/{at:%Y/%m/%d}"


def _upload(storage: Minio, bucket: str, key: str, data: bytes) -> None:
    try:
        storage.put_object(bucket, key, io.BytesIO(data), len(data), content_type="image/jpeg")
    except _STORAGE_ERRORS as exc:
        raise CaptureDeviceError(f"Could not upload {bucket}/{key}: {exc}") from exc


def read_object(bucket: str, key: str) -> bytes:
    """Fetch one stored image's bytes from MinIO."""
    try:
        response = get_minio_client().get_object(bucket, key)
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()
    except _STORAGE_ERRORS as exc:
        raise CaptureDeviceError(f"Could not read {bucket}/{key}: {exc}") from exc


def capture_for_plant(
    session: Session, plant_id: uuid.UUID, run_id: uuid.UUID | None = None
) -> CaptureResult:
    """Grab, check and store one frame for ``plant_id``.

    Raises :class:`CaptureUnavailable` when the plant is unknown, archived, has no
    ``device_id`` or its device is not in ``CAPTURE_WEBCAM_MAP`` (an empty map
    disables capture), :class:`CaptureBusy` when the device's webcam is already in
    use, and :class:`CaptureDeviceError` when the webcam or MinIO fails. A frame
    that fails the quality gate is still stored, with ``quality_passed=False``.
    """
    plant = get_plant(session, plant_id)
    if plant is None or not plant.is_active:
        raise CaptureUnavailable("Plant not found or archived")
    if plant.device_id is None:
        raise CaptureUnavailable("Plant has no device_id")
    webcams = parse_webcam_map(get_settings().capture_webcam_map)
    if not webcams:
        raise CaptureUnavailable("Capture is disabled (CAPTURE_WEBCAM_MAP is empty)")
    if plant.device_id not in webcams:
        raise CaptureUnavailable(f"Device {plant.device_id!r} is not in CAPTURE_WEBCAM_MAP")

    with _device_lock(plant.device_id):
        return _capture(session, plant_id, run_id, plant.device_id, webcams[plant.device_id])


def _capture(
    session: Session,
    plant_id: uuid.UUID,
    run_id: uuid.UUID | None,
    device_id: str,
    camera_index: int,
) -> CaptureResult:
    try:
        grabbed = capture_once(CameraConfig(camera_index=camera_index))
    except WebcamError as exc:
        raise CaptureDeviceError(str(exc)) from exc

    quality = assess_image_quality(grabbed.image, QualityConfig())
    reasons = list(quality.reasons)
    processed = None
    if quality.valid:
        try:
            processed = preprocess_image(grabbed.image, PreprocessConfig())
        except PreprocessError:
            reasons.append("plant_not_detected")

    prefix = _day_prefix(plant_id, grabbed.captured_at)
    capture_id = uuid.uuid4()
    capture = Capture(
        id=capture_id,
        plant_id=plant_id,
        run_id=run_id,
        device_id=device_id,
        captured_at=grabbed.captured_at,
        raw_object_key=f"{prefix}/{capture_id}.jpg",
        width=grabbed.width,
        height=grabbed.height,
        brightness=quality.brightness,
        blur_score=quality.blur_score,
        quality_passed=processed is not None,
        quality_reason=",".join(reasons) or None,
    )

    buckets = bucket_names()
    storage = get_minio_client()
    _upload(storage, buckets["captures"], capture.raw_object_key, grabbed.jpeg)

    frame = None
    if processed is not None:
        previous = latest_frames(session, plant_id, limit=1)
        frame_id = uuid.uuid4()
        frame = ProcessedFrame(
            id=frame_id,
            capture_id=capture_id,
            plant_id=plant_id,
            run_id=run_id,
            object_key=f"{prefix}/{frame_id}_{processed.crop_type}.jpg",
            crop_type=processed.crop_type,
            width=processed.output_width,
            height=processed.output_height,
            aligned_to_frame_id=previous[0][0].id if previous else None,
            processed_at=utcnow(),
        )
        _upload(storage, buckets["frames_derived"], frame.object_key, encode_jpeg(processed.image))

    session.add(capture)
    session.flush()
    if frame is not None:
        session.add(frame)
    session.commit()
    return CaptureResult(capture=capture, processed_frame=frame)


def latest_frames(
    session: Session,
    plant_id: uuid.UUID,
    *,
    limit: int,
    run_id: uuid.UUID | None = None,
    before: datetime | None = None,
) -> list[tuple[ProcessedFrame, datetime]]:
    """Processed frames newest first, each with its capture's ``captured_at``.

    ``run_id`` keeps only that run's frames; ``before`` only frames processed
    strictly earlier.
    """
    stmt = (
        select(ProcessedFrame, Capture.captured_at)
        .join(Capture, Capture.id == ProcessedFrame.capture_id)
        .where(ProcessedFrame.plant_id == plant_id)
    )
    if run_id is not None:
        stmt = stmt.where(ProcessedFrame.run_id == run_id)
    if before is not None:
        stmt = stmt.where(ProcessedFrame.processed_at < before)
    stmt = stmt.order_by(ProcessedFrame.processed_at.desc()).limit(limit)
    return [(frame, captured_at) for frame, captured_at in session.execute(stmt).all()]


def list_frames(session: Session, plant_id: uuid.UUID, limit: int) -> Sequence[ProcessedFrame]:
    """A plant's processed frames, newest first."""
    stmt = (
        select(ProcessedFrame)
        .where(ProcessedFrame.plant_id == plant_id)
        .order_by(ProcessedFrame.processed_at.desc())
        .limit(limit)
    )
    return session.scalars(stmt).all()
