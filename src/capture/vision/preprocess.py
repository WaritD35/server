from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2

from capture.vision.codec import Image


class PreprocessError(RuntimeError):
    """Error raised when an image cannot be preprocessed."""


@dataclass(frozen=True)
class PreprocessConfig:
    """Settings for the Phase 2 whole-plant preprocessing step."""

    crop_type: str = "whole_plant"
    crop_method: str = "green_mask_bbox"
    margin_ratio: float = 0.22
    bottom_margin_ratio: float = 0.35
    max_side: int = 1280
    min_mask_area_ratio: float = 0.01


@dataclass(frozen=True)
class CropBox:
    """Rectangle used to crop the raw image."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class PreprocessResult:
    """One processed whole-plant image and how it was produced."""

    image: Image
    crop_type: str
    crop_method: str
    bbox: CropBox
    margin_ratio: float
    bottom_margin_ratio: float
    source_width: int
    source_height: int
    output_width: int
    output_height: int


def build_green_mask(image: Any) -> Any:
    """Create a rough mask for green plant tissue using HSV color thresholds."""

    hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower_green = (35, 25, 20)
    upper_green = (95, 255, 255)
    mask = cv2.inRange(hsv_image, lower_green, upper_green)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def find_mask_bbox(mask: Any, config: PreprocessConfig) -> CropBox:
    """Find a bounding box around the detected plant mask."""

    image_height, image_width = mask.shape[:2]
    nonzero_pixels = cv2.findNonZero(mask)
    if nonzero_pixels is None:
        raise PreprocessError("Could not find green plant pixels in the image.")

    mask_area_ratio = len(nonzero_pixels) / float(image_width * image_height)
    if mask_area_ratio < config.min_mask_area_ratio:
        raise PreprocessError(
            f"Detected plant mask is too small: {mask_area_ratio:.4f}. "
            "Check lighting, framing, or mask thresholds."
        )

    x, y, width, height = cv2.boundingRect(nonzero_pixels)
    return CropBox(x=int(x), y=int(y), width=int(width), height=int(height))


def add_crop_margin(
    bbox: CropBox, image_width: int, image_height: int, config: PreprocessConfig
) -> CropBox:
    """Expand the crop so leaves, pot rim, and nearby context are not cut off."""

    horizontal_margin = int(bbox.width * config.margin_ratio)
    top_margin = int(bbox.height * config.margin_ratio)
    bottom_margin = int(bbox.height * config.bottom_margin_ratio)

    x1 = max(0, bbox.x - horizontal_margin)
    y1 = max(0, bbox.y - top_margin)
    x2 = min(image_width, bbox.x + bbox.width + horizontal_margin)
    y2 = min(image_height, bbox.y + bbox.height + bottom_margin)

    return CropBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1)


def crop_image(image: Any, bbox: CropBox) -> Any:
    """Crop the original image using the calculated crop box."""

    return image[bbox.y : bbox.y + bbox.height, bbox.x : bbox.x + bbox.width]


def resize_max_side(image: Any, max_side: int) -> Any:
    """Resize an image so its longest side is at most max_side."""

    height, width = image.shape[:2]
    longest_side = max(width, height)
    if longest_side <= max_side:
        return image

    scale = max_side / float(longest_side)
    output_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, output_size, interpolation=cv2.INTER_AREA)


def preprocess_image(source_image: Image, config: PreprocessConfig) -> PreprocessResult:
    """Create a whole-plant crop from a raw BGR image."""

    source_height, source_width = source_image.shape[:2]

    mask = build_green_mask(source_image)
    mask_bbox = find_mask_bbox(mask, config)
    crop_bbox = add_crop_margin(mask_bbox, source_width, source_height, config)
    cropped_image = crop_image(source_image, crop_bbox)
    processed_image = resize_max_side(cropped_image, config.max_side)

    output_height, output_width = processed_image.shape[:2]
    return PreprocessResult(
        image=processed_image,
        crop_type=config.crop_type,
        crop_method=config.crop_method,
        bbox=crop_bbox,
        margin_ratio=config.margin_ratio,
        bottom_margin_ratio=config.bottom_margin_ratio,
        source_width=int(source_width),
        source_height=int(source_height),
        output_width=int(output_width),
        output_height=int(output_height),
    )
