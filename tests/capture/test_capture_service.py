"""``capture_for_plant`` with a fake webcam and a fake MinIO."""

import uuid
from collections.abc import Callable

import pytest
from capture_fakes import FakeStorage, FakeWebcam, checkerboard, dark_image
from sqlalchemy import select
from sqlalchemy.orm import Session

from capture import service
from capture.models import Capture, ProcessedFrame
from capture.service import (
    CaptureBusy,
    CaptureDeviceError,
    CaptureUnavailable,
    capture_for_plant,
)
from capture.vision.codec import decode_image
from core.storage import bucket_names

BUCKETS = bucket_names()


def _captures(session: Session) -> list[Capture]:
    return list(session.scalars(select(Capture).order_by(Capture.captured_at)))


def _frames(session: Session) -> list[ProcessedFrame]:
    return list(session.scalars(select(ProcessedFrame).order_by(ProcessedFrame.processed_at)))


def test_passing_capture_stores_raw_and_processed(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    run_id = uuid.uuid4()
    result = capture_for_plant(session, plant_id, run_id)

    assert result.quality_passed is True
    assert result.quality_reason is None
    assert webcam.calls[0].camera_index == 0

    [capture] = _captures(session)
    [frame] = _frames(session)
    assert capture.id == result.capture.id
    assert (capture.plant_id, capture.run_id, capture.device_id) == (plant_id, run_id, "cam-1")
    assert (capture.width, capture.height) == (1280, 720)
    assert capture.brightness is not None and capture.blur_score is not None
    assert capture.raw_object_key == f"{plant_id}/2026/10/11/{capture.id}.jpg"

    assert frame.id == result.processed_frame.id  # type: ignore[union-attr]
    assert (frame.capture_id, frame.plant_id, frame.run_id) == (capture.id, plant_id, run_id)
    assert frame.crop_type == "whole_plant"
    assert frame.object_key == f"{plant_id}/2026/10/11/{frame.id}_whole_plant.jpg"
    assert frame.aligned_to_frame_id is None

    assert storage.keys(BUCKETS["captures"]) == [capture.raw_object_key]
    assert storage.keys(BUCKETS["frames_derived"]) == [frame.object_key]
    raw = decode_image(storage.objects[(BUCKETS["captures"], capture.raw_object_key)])
    processed = decode_image(storage.objects[(BUCKETS["frames_derived"], frame.object_key)])
    assert raw.shape == (720, 1280, 3)
    assert processed.shape == (frame.height, frame.width, 3)
    assert set(storage.content_types.values()) == {"image/jpeg"}


def test_next_frame_is_aligned_to_the_previous_one(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    first = capture_for_plant(session, plant_id)
    second = capture_for_plant(session, plant_id)

    assert first.processed_frame is not None and second.processed_frame is not None
    assert second.processed_frame.aligned_to_frame_id == first.processed_frame.id
    assert second.capture.run_id is None


def test_quality_failure_keeps_only_the_capture_row(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    webcam.image = dark_image()
    result = capture_for_plant(session, plant_id)

    assert result.quality_passed is False
    assert result.processed_frame is None
    [capture] = _captures(session)
    assert capture.quality_passed is False
    assert capture.quality_reason == "image_too_dark,image_too_blurry"
    assert _frames(session) == []
    assert storage.keys(BUCKETS["captures"]) == [capture.raw_object_key]
    assert storage.keys(BUCKETS["frames_derived"]) == []


def test_no_plant_in_frame_counts_as_quality_failure(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    webcam.image = checkerboard()
    result = capture_for_plant(session, plant_id)

    assert result.quality_passed is False
    assert result.quality_reason == "plant_not_detected"
    assert _frames(session) == []


@pytest.mark.parametrize(
    ("webcam_map", "device_id", "message"),
    [
        ("", "cam-1", "CAPTURE_WEBCAM_MAP is empty"),
        ("cam-2=1", "cam-1", "not in CAPTURE_WEBCAM_MAP"),
        ("cam-1=0", None, "no device_id"),
    ],
)
def test_unavailable_when_device_cannot_be_captured(
    session: Session,
    add_plant: Callable[..., uuid.UUID],
    set_webcam_map: Callable[[str], None],
    webcam: FakeWebcam,
    storage: FakeStorage,
    webcam_map: str,
    device_id: str | None,
    message: str,
) -> None:
    set_webcam_map(webcam_map)
    plant_id = add_plant(device_id)

    with pytest.raises(CaptureUnavailable, match=message):
        capture_for_plant(session, plant_id)
    assert webcam.calls == []
    assert _captures(session) == []


def test_unavailable_for_unknown_or_archived_plant(
    session: Session,
    add_plant: Callable[..., uuid.UUID],
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    with pytest.raises(CaptureUnavailable, match="not found or archived"):
        capture_for_plant(session, uuid.uuid4())
    with pytest.raises(CaptureUnavailable, match="not found or archived"):
        capture_for_plant(session, add_plant(archived=True))
    assert webcam.calls == []


def test_busy_while_another_capture_holds_the_device(
    session: Session,
    plant_id: uuid.UUID,
    add_plant: Callable[..., uuid.UUID],
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    other_plant = add_plant("cam-2")
    with service._device_lock("cam-1"):
        with pytest.raises(CaptureBusy, match="busy"):
            capture_for_plant(session, plant_id)
        assert webcam.calls == []
        # the lock is per device: another webcam is unaffected
        assert capture_for_plant(session, other_plant).quality_passed is True

    assert capture_for_plant(session, plant_id).quality_passed is True


def test_webcam_failure_raises_device_error_and_frees_the_lock(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    webcam.fail()
    with pytest.raises(CaptureDeviceError, match="Could not open webcam"):
        capture_for_plant(session, plant_id)
    assert _captures(session) == []

    webcam.error = None
    assert capture_for_plant(session, plant_id).quality_passed is True


def test_storage_failure_raises_device_error_and_stores_nothing(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    storage.down = True
    with pytest.raises(CaptureDeviceError, match="Could not upload"):
        capture_for_plant(session, plant_id)
    session.rollback()
    assert _captures(session) == []
    assert _frames(session) == []


def test_list_frames_newest_first_with_limit(
    session: Session, plant_id: uuid.UUID, webcam: FakeWebcam, storage: FakeStorage
) -> None:
    ids = [capture_for_plant(session, plant_id).processed_frame.id for _ in range(3)]  # type: ignore[union-attr]

    assert [f.id for f in service.list_frames(session, plant_id, 2)] == ids[::-1][:2]
    assert service.list_frames(session, uuid.uuid4(), 10) == []
