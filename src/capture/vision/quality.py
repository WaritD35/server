from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2

from capture.vision.codec import Image


@dataclass(frozen=True)
class QualityConfig:
    """Thresholds used to decide whether an image is good enough for the PoC."""

    min_width: int = 640
    min_height: int = 480
    min_brightness: float = 40.0
    max_brightness: float = 220.0
    min_blur_score: float = 100.0


@dataclass(frozen=True)
class QualityResult:
    """Image quality report for one input image."""

    valid: bool
    width: int
    height: int
    brightness: float
    blur_score: float
    reasons: list[str]


def check_resolution(width: int, height: int, config: QualityConfig) -> list[str]:
    """Check whether the image dimensions meet the minimum PoC resolution."""

    reasons: list[str] = []
    if width < config.min_width:
        reasons.append("image_too_narrow")
    if height < config.min_height:
        reasons.append("image_too_short")
    return reasons


def measure_brightness(gray_image: Any) -> float:
    """Measure average grayscale brightness from 0 to 255."""

    return float(gray_image.mean())


def check_brightness(brightness: float, config: QualityConfig) -> list[str]:
    """Check whether the image is too dark or too bright."""

    if brightness < config.min_brightness:
        return ["image_too_dark"]
    if brightness > config.max_brightness:
        return ["image_too_bright"]
    return []


def measure_blur(gray_image: Any) -> float:
    """Estimate sharpness with Laplacian variance; higher means sharper."""

    return float(cv2.Laplacian(gray_image, cv2.CV_64F).var())


def check_blur(blur_score: float, config: QualityConfig) -> list[str]:
    """Check whether the image appears too blurry for downstream vision work."""

    if blur_score < config.min_blur_score:
        return ["image_too_blurry"]
    return []


def assess_image_quality(image: Image, config: QualityConfig) -> QualityResult:
    """Run all PoC quality checks for one BGR image."""

    height, width = image.shape[:2]
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    brightness = measure_brightness(gray_image)
    blur_score = measure_blur(gray_image)

    reasons: list[str] = []
    reasons.extend(check_resolution(width, height, config))
    reasons.extend(check_brightness(brightness, config))
    reasons.extend(check_blur(blur_score, config))

    return QualityResult(
        valid=len(reasons) == 0,
        width=int(width),
        height=int(height),
        brightness=round(brightness, 2),
        blur_score=round(blur_score, 2),
        reasons=reasons,
    )
