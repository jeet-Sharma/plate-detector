"""Small immutable records shared between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in pixel coordinates (x1, y1 top-left; x2, y2 bottom-right)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0

    def intersection(self, other: Box) -> float:
        w = min(self.x2, other.x2) - max(self.x1, other.x1)
        h = min(self.y2, other.y2) - max(self.y1, other.y1)
        return max(0.0, w) * max(0.0, h)

    def iou(self, other: Box) -> float:
        inter = self.intersection(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def intersection_over_min(self, other: Box) -> float:
        smaller = min(self.area, other.area)
        return self.intersection(other) / smaller if smaller > 0 else 0.0


@dataclass(frozen=True)
class Detection:
    box: Box
    score: float


@dataclass(frozen=True)
class PlateAlphabet:
    """Character set of the OCR model: one probability column per character."""

    chars: str
    pad: str = "_"

    @property
    def pad_index(self) -> int:
        return self.chars.index(self.pad)

    def decode(self, probs: np.ndarray) -> str:
        """Argmax string up to the first padding slot."""
        text = "".join(self.chars[i] for i in np.argmax(probs, axis=-1))
        return text.split(self.pad, 1)[0]


@dataclass(frozen=True, eq=False)
class Reading:
    """One OCR result for one plate in one frame."""

    timestamp: float
    box: Box
    probs: np.ndarray  # (slots, alphabet size), rows sum to 1
    confidence: float  # detector score x mean OCR character probability
    weight: float  # confidence x sharpness factor, used when voting


@dataclass(frozen=True)
class PlateResult:
    plate: str
    first_seen: float
    last_seen: float
    confidence: float
    readings: int
    matches_format: bool = True
    track_ids: tuple[int, ...] = ()  # tracks merged into this result, largest first
    char_confidence: tuple[float, ...] = ()  # winning vote share of each character
