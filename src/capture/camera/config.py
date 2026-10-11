from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraConfig:
    camera_index: int | None = None
    max_camera_index: int = 5
    warmup_frames: int = 5
    requested_width: int = 1280
    requested_height: int = 720
    jpeg_quality: int = 90


def parse_webcam_map(raw: str) -> dict[str, int]:
    """Parse ``CAPTURE_WEBCAM_MAP`` (``"device-abc=0,device-def=1"``) into device → index.

    Blank entries are ignored, so an empty string means no device is mapped.
    """

    mapping: dict[str, int] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        device_id, sep, index = entry.partition("=")
        device_id, index = device_id.strip(), index.strip()
        if not sep or not device_id or not index.isdigit():
            raise ValueError(f"Malformed CAPTURE_WEBCAM_MAP entry: {entry!r}")
        mapping[device_id] = int(index)
    return mapping
