"""Round-trip of the ``vision`` tables on in-memory SQLite."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from capture.db import VISION_SCHEMA, Base
from capture.models import Capture, ProcessedFrame


def test_capture_and_processed_frame_round_trip() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={VISION_SCHEMA: None})
    Base.metadata.create_all(engine)
    plant_id, run_id = uuid.uuid4(), uuid.uuid4()
    captured_at = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    try:
        with Session(engine) as session:
            capture = Capture(
                plant_id=plant_id,
                run_id=run_id,
                device_id="cam-1",
                captured_at=captured_at,
                raw_object_key=f"{plant_id}/2026/10/11/raw.jpg",
                width=1280,
                height=720,
                brightness=120.5,
                blur_score=310.0,
                quality_passed=True,
            )
            session.add(capture)
            session.flush()
            frame = ProcessedFrame(
                capture_id=capture.id,
                plant_id=plant_id,
                run_id=run_id,
                object_key=f"{plant_id}/2026/10/11/frame_full.jpg",
                crop_type="full",
                width=1024,
                height=1024,
                processed_at=captured_at,
            )
            session.add(frame)
            session.commit()
            capture_id, frame_id = capture.id, frame.id

        with Session(engine) as session:
            stored = session.get(Capture, capture_id)
            assert stored is not None
            assert stored.device_id == "cam-1"
            assert stored.quality_passed is True
            assert stored.quality_reason is None
            assert stored.created_at is not None
            frames = session.scalars(
                select(ProcessedFrame).where(ProcessedFrame.capture_id == capture_id)
            ).all()
            assert [f.id for f in frames] == [frame_id]
            assert frames[0].plant_id == plant_id
            assert frames[0].aligned_to_frame_id is None
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_indexes_and_foreign_keys() -> None:
    capture = Base.metadata.tables["vision.capture"]
    frame = Base.metadata.tables["vision.processed_frame"]
    assert {ix.name for ix in capture.indexes} == {"ix_capture_plant_ts"}
    assert {ix.name for ix in frame.indexes} == {"ix_processed_frame_plant_ts"}
    assert not capture.foreign_keys
    assert {fk.target_fullname for fk in frame.foreign_keys} == {"vision.capture.id"}
