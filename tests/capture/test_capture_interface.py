"""Frame-pair selection and frame reads through ``capture.interface``."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from capture_fakes import FakeStorage
from sqlalchemy.orm import Session

from capture.interface import get_frame_pair, read_frame
from capture.models import Capture, ProcessedFrame
from capture.service import CaptureDeviceError
from core.storage import bucket_names

T0 = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)


def _frame(
    session: Session, plant_id: uuid.UUID, minutes: int, run_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Store a capture and its processed frame ``minutes`` after ``T0``."""
    at = T0 + timedelta(minutes=minutes)
    capture = Capture(
        plant_id=plant_id,
        run_id=run_id,
        device_id="cam-1",
        captured_at=at,
        raw_object_key=f"{plant_id}/raw-{minutes}.jpg",
        width=1280,
        height=720,
        quality_passed=True,
    )
    session.add(capture)
    session.flush()
    frame = ProcessedFrame(
        capture_id=capture.id,
        plant_id=plant_id,
        run_id=run_id,
        object_key=f"{plant_id}/frame-{minutes}_whole_plant.jpg",
        crop_type="whole_plant",
        width=640,
        height=640,
        processed_at=at + timedelta(seconds=2),
    )
    session.add(frame)
    session.commit()
    return frame.id


def test_none_with_fewer_than_two_frames(session: Session) -> None:
    plant_id = uuid.uuid4()
    assert get_frame_pair(session, plant_id) is None

    _frame(session, plant_id, 0)
    assert get_frame_pair(session, plant_id) is None


def test_latest_two_frames_without_run_id(session: Session) -> None:
    plant_id, other_plant = uuid.uuid4(), uuid.uuid4()
    _frame(session, plant_id, 0)
    middle = _frame(session, plant_id, 10)
    latest = _frame(session, plant_id, 20)
    _frame(session, other_plant, 30)

    pair = get_frame_pair(session, plant_id)

    assert pair is not None
    assert (pair.previous.id, pair.current.id) == (middle, latest)
    assert pair.current.bucket == bucket_names()["frames_derived"]
    assert pair.current.object_key == f"{plant_id}/frame-20_whole_plant.jpg"
    assert pair.current.captured_at.replace(tzinfo=UTC) == T0 + timedelta(minutes=20)


def test_run_id_selects_that_runs_frame_and_the_one_before(session: Session) -> None:
    plant_id, run_id = uuid.uuid4(), uuid.uuid4()
    first = _frame(session, plant_id, 0)
    of_run = _frame(session, plant_id, 10, run_id)
    later = _frame(session, plant_id, 20)

    pair = get_frame_pair(session, plant_id, run_id)
    assert pair is not None
    assert (pair.previous.id, pair.current.id) == (first, of_run)
    assert pair.current.run_id == run_id

    latest_pair = get_frame_pair(session, plant_id)
    assert latest_pair is not None
    assert (latest_pair.previous.id, latest_pair.current.id) == (of_run, later)


def test_run_id_without_an_earlier_frame_or_without_a_frame(session: Session) -> None:
    plant_id, run_id = uuid.uuid4(), uuid.uuid4()
    _frame(session, plant_id, 0, run_id)
    _frame(session, plant_id, 10)

    assert get_frame_pair(session, plant_id, run_id) is None
    assert get_frame_pair(session, plant_id, uuid.uuid4()) is None


def test_read_frame_returns_the_stored_bytes(session: Session, storage: FakeStorage) -> None:
    plant_id = uuid.uuid4()
    _frame(session, plant_id, 0)
    _frame(session, plant_id, 10)
    pair = get_frame_pair(session, plant_id)
    assert pair is not None
    storage.objects[(pair.current.bucket, pair.current.object_key)] = b"\xff\xd8current"

    assert read_frame(pair.current) == b"\xff\xd8current"

    storage.down = True
    with pytest.raises(CaptureDeviceError, match="Could not read"):
        read_frame(pair.current)
