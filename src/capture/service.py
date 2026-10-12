"""Frame ingest from planter devices, and frame queries, for the ``capture`` context.

No HTTP concerns here. A planter photographs its plant and uploads the JPEG;
:func:`ingest_frame` quality-checks it and, if it passes, crops the plant. Image
bytes go to MinIO (raw → ``captures``, processed → ``frames-derived``); nothing
is written to local disk. Object keys are generated here, never taken from a
caller.

Uploads arrive whenever the device sends them. The pipeline's capture stage
then **claims** the newest frame uploaded since the previous run
(:func:`claim_frame`); that frame and the previous run's frame are the pair
``assessment`` compares.
"""

import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from minio import Minio
from minio.error import MinioException
from sqlalchemy import select
from sqlalchemy.orm import Session
from urllib3.exceptions import HTTPError

from capture.db import utcnow
from capture.models import Capture, ProcessedFrame
from capture.vision.codec import ImageCodecError, decode_image, encode_jpeg
from capture.vision.preprocess import PreprocessConfig, PreprocessError, preprocess_image
from capture.vision.quality import QualityConfig, assess_image_quality
from core.storage import bucket_names, get_minio_client

_STORAGE_ERRORS = (MinioException, HTTPError)

#: Largest upload accepted, in bytes.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: How far ahead of the server clock a device's ``captured_at`` may be.
CLOCK_SKEW = timedelta(minutes=5)

_JPEG_MAGIC = b"\xff\xd8\xff"


class InvalidUpload(ValueError):  # noqa: N818 - reads as an outcome, not an error
    """The upload is not a usable JPEG, is too large, or is dated in the future."""


class CaptureDeviceError(RuntimeError):
    """Raised when object storage fails (storing an upload or reading a frame)."""


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


def _as_utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def ingest_frame(
    session: Session,
    plant_id: uuid.UUID,
    *,
    device_id: str,
    jpeg: bytes,
    captured_at: datetime | None = None,
) -> CaptureResult:
    """Check and store one uploaded photo of ``plant_id``.

    The caller has already resolved the plant (a device through ``registry``).
    ``captured_at`` is the device's clock; it defaults to receipt time and may be
    at most :data:`CLOCK_SKEW` ahead. A photo that fails the quality gate is
    still stored, with ``quality_passed=False`` and the reasons, so the device
    can be told to retake it. The frame is not tied to a run until the capture
    stage claims it.

    Raises :class:`InvalidUpload` for a non-JPEG, oversized or future-dated
    upload, and :class:`CaptureDeviceError` when MinIO fails.
    """
    now = utcnow()
    if len(jpeg) > MAX_UPLOAD_BYTES:
        raise InvalidUpload(f"upload is {len(jpeg)} bytes; the limit is {MAX_UPLOAD_BYTES}")
    if not jpeg.startswith(_JPEG_MAGIC):
        raise InvalidUpload("upload is not a JPEG image")
    taken_at = now if captured_at is None else _as_utc(captured_at)
    if taken_at > now + CLOCK_SKEW:
        raise InvalidUpload("captured_at is in the future")
    try:
        image = decode_image(jpeg)
    except ImageCodecError as exc:
        raise InvalidUpload(f"upload could not be decoded: {exc}") from exc

    quality = assess_image_quality(image, QualityConfig())
    reasons = list(quality.reasons)
    processed = None
    if quality.valid:
        try:
            processed = preprocess_image(image, PreprocessConfig())
        except PreprocessError:
            reasons.append("plant_not_detected")

    height, width = image.shape[:2]
    prefix = _day_prefix(plant_id, taken_at)
    capture_id = uuid.uuid4()
    capture = Capture(
        id=capture_id,
        plant_id=plant_id,
        run_id=None,
        device_id=device_id,
        captured_at=taken_at,
        raw_object_key=f"{prefix}/{capture_id}.jpg",
        width=int(width),
        height=int(height),
        brightness=quality.brightness,
        blur_score=quality.blur_score,
        quality_passed=processed is not None,
        quality_reason=",".join(reasons) or None,
    )

    buckets = bucket_names()
    storage = get_minio_client()
    _upload(storage, buckets["captures"], capture.raw_object_key, jpeg)

    frame = None
    if processed is not None:
        frame_id = uuid.uuid4()
        frame = ProcessedFrame(
            id=frame_id,
            capture_id=capture_id,
            plant_id=plant_id,
            run_id=None,
            object_key=f"{prefix}/{frame_id}_{processed.crop_type}.jpg",
            crop_type=processed.crop_type,
            width=processed.output_width,
            height=processed.output_height,
            aligned_to_frame_id=None,
            processed_at=now,
        )
        _upload(storage, buckets["frames_derived"], frame.object_key, encode_jpeg(processed.image))

    session.add(capture)
    session.flush()
    if frame is not None:
        session.add(frame)
    session.commit()
    return CaptureResult(capture=capture, processed_frame=frame)


def claim_frame(
    session: Session, plant_id: uuid.UUID, run_id: uuid.UUID
) -> ProcessedFrame | None:
    """Tie the newest frame uploaded since the previous run to ``run_id``.

    "Since the previous run" means captured after the last frame a run claimed.
    Older unclaimed frames stay unclaimed. The claimed frame is aligned to that
    previous run's frame, which is what ``assessment`` compares it with. Returns
    ``None`` when nothing new has arrived — the stage has nothing to do.
    """
    previous = _latest_claimed(session, plant_id)
    stmt = (
        select(ProcessedFrame, Capture)
        .join(Capture, Capture.id == ProcessedFrame.capture_id)
        .where(ProcessedFrame.plant_id == plant_id, ProcessedFrame.run_id.is_(None))
    )
    if previous is not None:
        stmt = stmt.where(Capture.captured_at > previous[1].captured_at)
    row = session.execute(
        stmt.order_by(Capture.captured_at.desc(), ProcessedFrame.processed_at.desc()).limit(1)
    ).first()
    if row is None:
        return None
    frame, capture = row
    frame.run_id = run_id
    frame.aligned_to_frame_id = previous[0].id if previous is not None else None
    capture.run_id = run_id
    session.commit()
    return frame


def _latest_claimed(
    session: Session, plant_id: uuid.UUID
) -> tuple[ProcessedFrame, Capture] | None:
    row = session.execute(
        select(ProcessedFrame, Capture)
        .join(Capture, Capture.id == ProcessedFrame.capture_id)
        .where(ProcessedFrame.plant_id == plant_id, ProcessedFrame.run_id.is_not(None))
        .order_by(Capture.captured_at.desc(), ProcessedFrame.processed_at.desc())
        .limit(1)
    ).first()
    return None if row is None else (row[0], row[1])


def latest_frames(
    session: Session,
    plant_id: uuid.UUID,
    *,
    limit: int,
    run_id: uuid.UUID | None = None,
    before: datetime | None = None,
    claimed_only: bool = False,
) -> list[tuple[ProcessedFrame, datetime]]:
    """Processed frames newest first (by capture time), each with its ``captured_at``.

    ``run_id`` keeps only that run's frames; ``before`` only frames captured
    strictly earlier; ``claimed_only`` only frames some run has claimed.
    """
    stmt = (
        select(ProcessedFrame, Capture.captured_at)
        .join(Capture, Capture.id == ProcessedFrame.capture_id)
        .where(ProcessedFrame.plant_id == plant_id)
    )
    if run_id is not None:
        stmt = stmt.where(ProcessedFrame.run_id == run_id)
    if before is not None:
        stmt = stmt.where(Capture.captured_at < before)
    if claimed_only:
        stmt = stmt.where(ProcessedFrame.run_id.is_not(None))
    stmt = stmt.order_by(Capture.captured_at.desc(), ProcessedFrame.processed_at.desc()).limit(
        limit
    )
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
