"""JPEG encode/decode for in-memory images (BGR ``uint8`` NumPy arrays)."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]


class ImageCodecError(RuntimeError):
    """Error raised when an image cannot be encoded or decoded."""


def encode_jpeg(image: Image, quality: int = 90) -> bytes:
    """Encode a BGR image as JPEG bytes."""

    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ImageCodecError("Could not encode image as JPEG.")
    return bytes(buffer.tobytes())


def decode_image(data: bytes) -> Image:
    """Decode encoded image bytes (JPEG, PNG, ...) into a BGR image."""

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageCodecError("Could not decode image bytes.")
    return np.asarray(image, dtype=np.uint8)
