"""``ingest_frame`` (device uploads) and ``claim_frame`` (the capture stage), with a fake MinIO."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from capture_fakes import FakeStorage, dark_jpeg, no_plant_jpeg, plant_jpeg
from sqlalchemy import select
from sqlalchemy.orm import Session

from capture import service
from capture.models import Capture, ProcessedFrame
from capture.service import (
    CaptureDeviceError,
    InvalidUpload,
    claim_frame,
    ingest_frame,
    list_frames,
)
from capture.vision.codec import decode_image
from core.storage import bucket_names

BUCKETS = bucket_names()
T0 = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)


def _captures(session: Session) -> list[Capture]:
    return list(session.scalars(select(Capture).order_by(Capture.captured_at)))


def _frames(session: Session) -> list[ProcessedFrame]:
    return list(session.scalars(select(ProcessedFrame).order_by(ProcessedFrame.processed_at)))


def _upload(session: Session, plant_id: uuid.UUID, minutes: int, jpeg: bytes | None = None):
    return ingest_frame(
        session,
        plant_id,
        device_id="cam-1",
        jpeg=jpeg if jpeg is not None else plant_jpeg(),
        captured_at=T0 + timedelta(minutes=minutes),
    )


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #


def test_a_good_photo_is_stored_raw_and_processed_without_a_run(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    jpeg = plant_jpeg()
    result = _upload(session, plant_id, 0, jpeg)

    assert result.quality_passed is True
    assert result.quality_reason is None

    [capture] = _captures(session)
    [frame] = _frames(session)
    assert (capture.plant_id, capture.run_id, capture.device_id) == (plant_id, None, "cam-1")
    assert capture.captured_at.replace(tzinfo=UTC) == T0
    assert (capture.width, capture.height) == (1280, 720)
    assert capture.brightness is not None and capture.blur_score is not None
    assert capture.raw_object_key == f"{plant_id}/2026/10/11/{capture.id}.jpg"

    assert (frame.capture_id, frame.plant_id, frame.run_id) == (capture.id, plant_id, None)
    assert frame.crop_type == "whole_plant"
    assert frame.object_key == f"{plant_id}/2026/10/11/{frame.id}_whole_plant.jpg"
    assert frame.aligned_to_frame_id is None

    # The raw object is exactly what the device sent.
    assert storage.objects[(BUCKETS["captures"], capture.raw_object_key)] == jpeg
    processed = decode_image(storage.objects[(BUCKETS["frames_derived"], frame.object_key)])
    assert processed.shape[0] < 720 and processed.shape[1] < 1280
    assert storage.content_types[(BUCKETS["frames_derived"], frame.object_key)] == "image/jpeg"


def test_a_dark_photo_is_kept_with_its_reasons_but_not_processed(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    result = _upload(session, plant_id, 0, dark_jpeg())

    assert result.quality_passed is False
    assert "image_too_dark" in (result.quality_reason or "")
    assert result.processed_frame is None
    assert len(_captures(session)) == 1
    assert _frames(session) == []
    assert storage.keys(BUCKETS["frames_derived"]) == []


def test_no_plant_in_frame_counts_as_a_quality_failure(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    result = _upload(session, plant_id, 0, no_plant_jpeg())

    assert result.quality_passed is False
    assert result.quality_reason == "plant_not_detected"


def test_captured_at_defaults_to_receipt_time(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    before = datetime.now(UTC)
    result = ingest_frame(session, plant_id, device_id="cam-1", jpeg=plant_jpeg())

    assert result.capture.captured_at.replace(tzinfo=UTC) >= before - timedelta(seconds=1)


@pytest.mark.parametrize(
    ("jpeg", "captured_at", "message"),
    [
        (b"\x89PNG\r\n\x1a\n not a jpeg", None, "not a JPEG"),
        (b"\xff\xd8\xff garbage", None, "could not be decoded"),
        (None, datetime.now(UTC) + timedelta(hours=1), "future"),
    ],
    ids=["png", "corrupt-jpeg", "future-captured-at"],
)
def test_unusable_uploads_are_rejected_and_nothing_is_stored(
    session: Session,
    plant_id: uuid.UUID,
    storage: FakeStorage,
    jpeg: bytes | None,
    captured_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(InvalidUpload, match=message):
        ingest_frame(
            session,
            plant_id,
            device_id="cam-1",
            jpeg=jpeg if jpeg is not None else plant_jpeg(),
            captured_at=captured_at,
        )

    assert _captures(session) == []
    assert storage.objects == {}


def test_an_oversized_upload_is_rejected(
    session: Session,
    plant_id: uuid.UUID,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 100)

    with pytest.raises(InvalidUpload, match="limit"):
        _upload(session, plant_id, 0)

    assert _captures(session) == []


def test_storage_failure_raises_and_stores_nothing(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    storage.down = True

    with pytest.raises(CaptureDeviceError, match="Could not upload"):
        _upload(session, plant_id, 0)

    session.rollback()
    assert _captures(session) == []


# --------------------------------------------------------------------------- #
# claim (the capture stage)
# --------------------------------------------------------------------------- #


def test_the_newest_upload_since_the_last_run_is_claimed(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    _upload(session, plant_id, 0)
    newest = _upload(session, plant_id, 30).processed_frame
    _upload(session, plant_id, 10)  # arrives late, but was taken earlier
    run_id = uuid.uuid4()

    claimed = claim_frame(session, plant_id, run_id)

    assert claimed is not None and newest is not None
    assert claimed.id == newest.id
    assert claimed.run_id == run_id
    assert claimed.aligned_to_frame_id is None  # the plant's first run
    capture = session.get(Capture, claimed.capture_id)
    assert capture is not None and capture.run_id == run_id


def test_a_later_run_claims_only_newer_photos_and_aligns_to_the_last_run(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    first = _upload(session, plant_id, 0).processed_frame
    assert first is not None
    claim_frame(session, plant_id, uuid.uuid4())

    _upload(session, plant_id, -10)  # older than the last claimed photo
    assert claim_frame(session, plant_id, uuid.uuid4()) is None

    newer = _upload(session, plant_id, 60).processed_frame
    claimed = claim_frame(session, plant_id, uuid.uuid4())

    assert claimed is not None and newer is not None
    assert claimed.id == newer.id
    assert claimed.aligned_to_frame_id == first.id


def test_nothing_to_claim_without_a_passing_upload(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    assert claim_frame(session, plant_id, uuid.uuid4()) is None

    _upload(session, plant_id, 0, dark_jpeg())
    assert claim_frame(session, plant_id, uuid.uuid4()) is None


def test_list_frames_newest_first_with_limit(
    session: Session, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    for minutes in (0, 10, 20):
        _upload(session, plant_id, minutes)

    frames = list_frames(session, plant_id, limit=2)

    assert len(frames) == 2
    assert frames[0].processed_at >= frames[1].processed_at
