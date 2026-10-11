from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np

from capture.camera.config import CameraConfig
from capture.vision.codec import Image, ImageCodecError, encode_jpeg


class WebcamError(RuntimeError):
    """Error raised for webcam-specific failures, such as open or read errors."""


@dataclass(frozen=True)
class CapturedImage:
    """One frame grabbed from a webcam, decoded and JPEG-encoded."""

    camera_index: int
    captured_at: datetime
    image: Image
    jpeg: bytes
    width: int
    height: int


def video_backend() -> int:
    """Choose the OpenCV video backend for the current operating system."""

    if platform.system() == "Windows":
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def open_camera(camera_index: int) -> cv2.VideoCapture:
    """Open the webcam at the requested index, failing fast if unavailable."""

    camera = cv2.VideoCapture(camera_index, video_backend())
    if not camera.isOpened():
        camera.release()
        raise WebcamError(f"Could not open webcam at index {camera_index}.")
    return camera


def apply_camera_settings(camera: cv2.VideoCapture, config: CameraConfig) -> None:
    """Ask the webcam to use the configured capture resolution."""

    camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.requested_width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.requested_height)


def detect_camera(config: CameraConfig) -> tuple[int, cv2.VideoCapture]:
    """Find the first webcam index that opens and returns a readable frame."""

    for camera_index in range(config.max_camera_index + 1):
        camera = cv2.VideoCapture(camera_index, video_backend())
        if not camera.isOpened():
            camera.release()
            continue

        apply_camera_settings(camera, config)

        success, frame = camera.read()
        if success and frame is not None:
            return camera_index, camera

        camera.release()

    raise WebcamError(
        "No readable webcam was found. Check that the USB webcam is connected "
        "and not already in use by another application."
    )


def read_single_frame(camera: cv2.VideoCapture, warmup_frames: int) -> Any:
    """Read one frame after giving the camera a few frames to warm up."""

    frame = None
    attempts = max(1, warmup_frames + 1)

    for _ in range(attempts):
        success, current_frame = camera.read()
        if not success or current_frame is None:
            raise WebcamError("Could not read a frame from the webcam.")
        frame = current_frame

    return frame


def to_captured_image(
    frame: Any, *, camera_index: int, captured_at: datetime, jpeg_quality: int = 90
) -> CapturedImage:
    """Wrap a raw frame with its JPEG encoding and dimensions."""

    image = np.asarray(frame, dtype=np.uint8)
    try:
        jpeg = encode_jpeg(image, jpeg_quality)
    except ImageCodecError as error:
        raise WebcamError(str(error)) from error

    height, width = image.shape[:2]
    return CapturedImage(
        camera_index=camera_index,
        captured_at=captured_at,
        image=image,
        jpeg=jpeg,
        width=int(width),
        height=int(height),
    )


def capture_once(config: CameraConfig) -> CapturedImage:
    """Open a webcam, grab one frame in memory, and release the camera."""

    if config.camera_index is None:
        camera_index, camera = detect_camera(config)
    else:
        camera_index = config.camera_index
        camera = open_camera(camera_index)
        apply_camera_settings(camera, config)

    try:
        frame = read_single_frame(camera, config.warmup_frames)
        captured_at = datetime.now(UTC)
    finally:
        camera.release()

    return to_captured_image(
        frame,
        camera_index=camera_index,
        captured_at=captured_at,
        jpeg_quality=config.jpeg_quality,
    )
