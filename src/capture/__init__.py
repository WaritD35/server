"""Capture & Vision (context 2).

Receives the photos planter devices upload, quality-checks them, crops the plant
and stores raw and processed images in MinIO. Owns the ``vision`` Postgres
schema.

Public surface:

- :data:`router` — the ``/capture`` endpoints, mounted by ``main_web``: device
  upload (``POST /capture/devices/me/frames``) and superuser upload / listing.
- :func:`claim_frame` — the pipeline's capture stage, called by
  ``orchestrator``: ties the newest frame uploaded since the previous run to
  the run, or returns ``None`` when nothing new arrived.
- :func:`get_frame_pair`, :func:`read_frame` — in-process interface for the
  other contexts; frames come back as :class:`FrameRef` / :class:`FramePair`.
  :class:`CaptureDeviceError` is the storage failure those calls raise.
"""

from capture.api import router
from capture.interface import CaptureDeviceError, get_frame_pair, read_frame
from capture.schemas import FramePair, FrameRef
from capture.service import claim_frame

__all__ = [
    "CaptureDeviceError",
    "FramePair",
    "FrameRef",
    "claim_frame",
    "get_frame_pair",
    "read_frame",
    "router",
]
