"""Quality gate, whole-plant crop, comparison canvas and codec on synthetic images."""

import numpy as np
import pytest
from capture_fakes import PLANT_BOX, checkerboard, dark_image, plant_image

from capture.vision.alignment import AlignmentConfig, create_comparison_canvas
from capture.vision.codec import ImageCodecError, decode_image, encode_jpeg
from capture.vision.preprocess import PreprocessConfig, PreprocessError, preprocess_image
from capture.vision.quality import QualityConfig, assess_image_quality


def test_sharp_well_lit_image_passes_quality() -> None:
    result = assess_image_quality(plant_image(), QualityConfig())

    assert result.valid is True
    assert result.reasons == []
    assert (result.width, result.height) == (1280, 720)
    assert 40 <= result.brightness <= 220
    assert result.blur_score >= 100


def test_dark_flat_image_is_too_dark_and_too_blurry() -> None:
    result = assess_image_quality(dark_image(), QualityConfig())

    assert result.valid is False
    assert result.reasons == ["image_too_dark", "image_too_blurry"]
    assert result.blur_score == 0.0


def test_small_image_fails_resolution() -> None:
    result = assess_image_quality(checkerboard(240, 320), QualityConfig())

    assert result.reasons == ["image_too_narrow", "image_too_short"]


def test_bright_image_fails_brightness() -> None:
    image = np.full((720, 1280, 3), 250, dtype=np.uint8)
    image[::2] = 235

    assert "image_too_bright" in assess_image_quality(image, QualityConfig()).reasons


def test_preprocess_crops_around_the_plant_with_margins() -> None:
    config = PreprocessConfig()
    result = preprocess_image(plant_image(), config)

    x, y, w, h = PLANT_BOX
    side = int(w * config.margin_ratio)
    bottom = int(h * config.bottom_margin_ratio)
    assert (result.bbox.x, result.bbox.y) == (x - side, y - side)
    assert result.bbox.width == w + 2 * side
    assert result.bbox.height == h + side + bottom
    assert result.image.shape == (result.bbox.height, result.bbox.width, 3)
    assert (result.output_width, result.output_height) == (result.bbox.width, result.bbox.height)
    assert (result.source_width, result.source_height) == (1280, 720)
    assert result.crop_type == "whole_plant"


def test_preprocess_caps_the_longest_side() -> None:
    result = preprocess_image(plant_image(), PreprocessConfig(max_side=200))

    assert max(result.output_width, result.output_height) == 200
    assert result.image.shape[:2] == (result.output_height, result.output_width)


def test_preprocess_without_green_raises() -> None:
    with pytest.raises(PreprocessError, match="green plant pixels"):
        preprocess_image(checkerboard(), PreprocessConfig())


def test_preprocess_rejects_a_tiny_plant() -> None:
    image = checkerboard()
    image[300:320, 600:620] = (40, 170, 40)

    with pytest.raises(PreprocessError, match="too small"):
        preprocess_image(image, PreprocessConfig())


def test_comparison_canvas_puts_both_images_side_by_side() -> None:
    config = AlignmentConfig(target_image_height=100)
    canvas = create_comparison_canvas(plant_image(), checkerboard(360, 640), config)

    assert canvas.canvas_type == "side_by_side"
    assert canvas.reference_shape.width == 1280
    assert canvas.current_shape.height == 360
    panel_width = 1280 * 100 // 720
    assert canvas.canvas_shape.width == 2 * config.padding + 2 * panel_width + config.gap
    assert canvas.canvas_shape.height == 2 * config.padding + config.label_height + 100
    assert canvas.image.shape == (canvas.canvas_shape.height, canvas.canvas_shape.width, 3)


def test_jpeg_round_trip_keeps_shape() -> None:
    data = encode_jpeg(plant_image())

    assert data[:2] == b"\xff\xd8"
    assert decode_image(data).shape == (720, 1280, 3)


def test_decode_rejects_non_images() -> None:
    with pytest.raises(ImageCodecError):
        decode_image(b"not an image")

