"""Synthetic images, a fake webcam and a fake MinIO for the ``capture`` tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from urllib3.exceptions import MaxRetryError

from capture.camera.config import CameraConfig
from capture.camera.webcam import CapturedImage, WebcamError, to_captured_image
from capture.vision.codec import Image

#: Where :func:`plant_image` paints the plant: rows 200–520, columns 480–800.
PLANT_BOX = (480, 200, 320, 320)


def checkerboard(height: int = 720, width: int = 1280) -> Image:
    """A sharp, mid-grey, colourless background (passes brightness and blur)."""
    yy, xx = np.indices((height, width))
    board = ((yy // 8 + xx // 8) % 2).astype(bool)
    grey = np.where(board, 160, 100).astype(np.uint8)
    return np.repeat(grey[..., None], 3, axis=2)


def plant_image(height: int = 720, width: int = 1280) -> Image:
    """:func:`checkerboard` with a green square standing in for the plant."""
    image = checkerboard(height, width)
    x, y, w, h = PLANT_BOX
    image[y : y + h, x : x + w] = (40, 170, 40)
    return image


def dark_image(height: int = 720, width: int = 1280) -> Image:
    """Flat and nearly black: too dark and too blurry."""
    return np.full((height, width, 3), 10, dtype=np.uint8)


@dataclass
class FakeWebcam:
    """Stands in for :func:`capture.camera.webcam.capture_once`."""

    image: Image = field(default_factory=plant_image)
    error: Exception | None = None
    calls: list[CameraConfig] = field(default_factory=list)
    start: datetime = datetime(2026, 10, 11, 7, 5, tzinfo=UTC)

    def __call__(self, config: CameraConfig) -> CapturedImage:
        self.calls.append(config)
        if self.error is not None:
            raise self.error
        assert config.camera_index is not None
        return to_captured_image(
            self.image,
            camera_index=config.camera_index,
            captured_at=self.start + timedelta(minutes=len(self.calls)),
        )

    def fail(self, message: str = "Could not open webcam at index 0.") -> None:
        self.error = WebcamError(message)


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


@dataclass
class FakeStorage:
    """The two :class:`minio.Minio` calls ``capture`` makes, kept in a dict."""

    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)
    content_types: dict[tuple[str, str], str] = field(default_factory=dict)
    down: bool = False

    def _check(self) -> None:
        if self.down:
            raise MaxRetryError(None, "http://minio:9000")  # type: ignore[arg-type]

    def put_object(
        self, bucket: str, key: str, data: Any, length: int, content_type: str = ""
    ) -> None:
        self._check()
        payload = data.read()
        assert len(payload) == length
        self.objects[(bucket, key)] = payload
        self.content_types[(bucket, key)] = content_type

    def get_object(self, bucket: str, key: str) -> _Response:
        self._check()
        return _Response(self.objects[(bucket, key)])

    def keys(self, bucket: str) -> list[str]:
        return [key for b, key in self.objects if b == bucket]
