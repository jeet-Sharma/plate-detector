import numpy as np
import pytest

from anpr.crop import crop_plate, enhance, sharpness, to_ocr_input
from anpr.types import Box

pytestmark = pytest.mark.unit


def frame(h: int = 200, w: int = 400) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def test_crop_adds_padding_and_returns_a_copy():
    img = frame()
    crop = crop_plate(img, Box(100, 100, 200, 150), padding=0.1)
    assert crop is not None
    assert crop.shape[:2] == (60, 120)  # 50x100 box plus 10% each side
    crop[:] = 0
    assert img[100:150, 100:200].any()  # the frame was not modified


def test_crop_touching_the_frame_edge_is_clamped():
    img = frame()
    crop = crop_plate(img, Box(-30, 170, 60, 230), padding=0.1)
    assert crop is not None
    assert crop.shape[0] > 0 and crop.shape[1] > 0
    assert crop.shape[0] <= 200 and crop.shape[1] <= 400


def test_crop_entirely_outside_the_frame_is_none():
    assert crop_plate(frame(), Box(500, 500, 600, 560)) is None
    assert crop_plate(frame(), Box(10, 10, 10.5, 10.5), padding=0.0) is None


def test_ocr_input_has_model_shape_and_dtype():
    out = to_ocr_input(frame(60, 90), 64, 128)
    assert out.shape == (64, 128, 3)
    assert out.dtype == np.uint8


def test_ocr_input_handles_downscaling_a_large_crop():
    assert to_ocr_input(frame(300, 600), 64, 128).shape == (64, 128, 3)


def test_enhance_keeps_shape_and_handles_a_flat_black_crop():
    out = enhance(np.zeros((20, 40, 3), dtype=np.uint8))
    assert out.shape == (20, 40, 3)


def test_sharpness_ranks_sharp_above_flat():
    flat = np.full((40, 80, 3), 128, dtype=np.uint8)
    assert sharpness(frame(40, 80)) > sharpness(flat)
