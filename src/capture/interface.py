"""Published in-process interface for other bounded contexts.

``assessment`` compares a plant's two most recent processed frames: it gets them
from :func:`get_frame_pair` and their bytes from :func:`read_frame` instead of
reaching into the ``vision`` schema or the ``frames-derived`` bucket.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from capture import service
from capture.models import ProcessedFrame
from capture.schemas import FramePair, FrameRef, ProcessedFrameRead
from capture.service import CaptureDeviceError
from core.storage import bucket_names

__all__ = ["CaptureDeviceError", "FramePair", "FrameRef", "get_frame_pair", "read_frame"]


def _frame_ref(frame: ProcessedFrame, captured_at: datetime, bucket: str) -> FrameRef:
    read = ProcessedFrameRead.model_validate(frame)
    return FrameRef(**read.model_dump(), bucket=bucket, captured_at=captured_at)


def get_frame_pair(
    session: Session, plant_id: uuid.UUID, run_id: uuid.UUID | None = None
) -> FramePair | None:
    """The frames to compare for ``plant_id``, or ``None`` when fewer than two exist.

    With ``run_id``, ``current`` is that run's processed frame and ``previous``
    the latest one processed before it; without, the latest two processed frames.
    """
    if run_id is None:
        rows = service.latest_frames(session, plant_id, limit=2)
        if len(rows) < 2:
            return None
        current, previous = rows
    else:
        own = service.latest_frames(session, plant_id, limit=1, run_id=run_id)
        if not own:
            return None
        current = own[0]
        earlier = service.latest_frames(session, plant_id, limit=1, before=current[0].processed_at)
        if not earlier:
            return None
        previous = earlier[0]

    bucket = bucket_names()["frames_derived"]
    return FramePair(
        previous=_frame_ref(*previous, bucket),
        current=_frame_ref(*current, bucket),
    )


def read_frame(frame: FrameRef) -> bytes:
    """The JPEG bytes of ``frame``; raises ``CaptureDeviceError`` if MinIO fails."""
    return service.read_object(frame.bucket, frame.object_key)
