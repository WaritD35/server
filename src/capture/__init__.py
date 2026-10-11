"""Capture & Vision (context 2).

Grabs a frame from the webcam mapped to a plant's device, quality-checks it,
crops the plant and stores raw and processed images in MinIO. Owns the
``vision`` Postgres schema.

Public surface:

- :data:`router` — the ``/capture`` debug endpoints (superuser), mounted by
  ``main_web``.
- :func:`get_frame_pair`, :func:`read_frame` — in-process interface for the
  other contexts; frames come back as :class:`FrameRef` / :class:`FramePair`.
  :class:`CaptureDeviceError` is the storage or webcam failure those calls raise.
"""

from capture.api import router
from capture.interface import CaptureDeviceError, get_frame_pair, read_frame
from capture.schemas import FramePair, FrameRef

__all__ = [
    "CaptureDeviceError",
    "FramePair",
    "FrameRef",
    "get_frame_pair",
    "read_frame",
    "router",
]
