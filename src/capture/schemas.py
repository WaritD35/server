"""Response models for the ``capture`` HTTP API and the published interface.

:class:`FrameRef` / :class:`FramePair` are what other contexts receive from
:mod:`capture.interface`; frames are referenced by id and object key, never by
a file path.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProcessedFrameRead(BaseModel):
    """One ``vision.processed_frame`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    capture_id: uuid.UUID
    plant_id: uuid.UUID
    run_id: uuid.UUID | None
    object_key: str
    crop_type: str | None
    width: int
    height: int
    aligned_to_frame_id: uuid.UUID | None
    processed_at: datetime


class CaptureRead(BaseModel):
    """The outcome of one uploaded photo; ``processed_frame`` is ``None`` on quality failure.

    ``quality_reason`` says why (``image_too_dark``, ``plant_not_detected``, …)
    so the device can retake the photo.
    """

    capture_id: uuid.UUID
    plant_id: uuid.UUID
    captured_at: datetime
    quality_passed: bool
    quality_reason: str | None
    brightness: float | None
    blur_score: float | None
    processed_frame: ProcessedFrameRead | None


class FrameRef(ProcessedFrameRead):
    """A processed frame plus where its bytes live and when it was captured."""

    model_config = ConfigDict(frozen=True)

    bucket: str
    captured_at: datetime


class FramePair(BaseModel):
    """Two processed frames of one plant, ``previous`` older than ``current``."""

    model_config = ConfigDict(frozen=True)

    previous: FrameRef
    current: FrameRef
