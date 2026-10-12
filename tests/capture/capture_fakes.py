"""Synthetic images and a fake MinIO for the ``capture`` tests."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from urllib3.exceptions import MaxRetryError

from capture.vision.codec import Image, encode_jpeg

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


def plant_jpeg() -> bytes:
    """What a planter uploads when the photo is good."""
    return encode_jpeg(plant_image())


def no_plant_jpeg() -> bytes:
    """Sharp and well lit, but nothing green in frame."""
    return encode_jpeg(checkerboard())


def dark_jpeg() -> bytes:
    """Fails the quality gate."""
    return encode_jpeg(dark_image())


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
