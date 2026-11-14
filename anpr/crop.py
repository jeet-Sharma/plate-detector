"""Cropping from the full-resolution frame and OCR preparation."""

from __future__ import annotations

import cv2
import numpy as np

from anpr.types import Box

_GAIN_RANGE = (0.6, 1.8)


def crop_plate(frame: np.ndarray, box: Box, padding: float = 0.08) -> np.ndarray | None:
    """Copy of the box region plus `padding` (fraction of box size), clamped to the frame.

    Returns None when the clamped region is empty. The copy lets the caller free the frame.
    """
    height, width = frame.shape[:2]
    pad_x, pad_y = box.width * padding, box.height * padding
    x1 = max(0, int(np.floor(box.x1 - pad_x)))
    y1 = max(0, int(np.floor(box.y1 - pad_y)))
    x2 = min(width, int(np.ceil(box.x2 + pad_x)))
    y2 = min(height, int(np.ceil(box.y2 + pad_y)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return frame[y1:y2, x1:x2].copy()


def sharpness(crop: np.ndarray) -> float:
    """Variance of the Laplacian of the grey crop: higher means sharper."""
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def enhance(crop: np.ndarray) -> np.ndarray:
    """Gray-world white balance (removes the blue cast), then CLAHE on lightness."""
    means = crop.reshape(-1, 3).mean(axis=0)
    gains = np.clip(means.mean() / np.maximum(means, 1.0), *_GAIN_RANGE)
    balanced = np.clip(crop.astype(np.float32) * gains, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(balanced, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def to_ocr_input(crop: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resize a BGR crop to the OCR input size and return RGB uint8 (height, width, 3)."""
    upscaling = crop.shape[1] < width
    method = cv2.INTER_CUBIC if upscaling else cv2.INTER_AREA
    resized = cv2.resize(crop, (width, height), interpolation=method)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
