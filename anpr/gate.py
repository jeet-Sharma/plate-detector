"""Scene-change gate: skip detection and OCR when the picture has not changed."""

from __future__ import annotations

import numpy as np


class ChangeGate:
    """Compare each thumbnail with the last *processed* one (mean absolute difference).

    A frame is processed when the difference reaches `threshold`, or after
    `max_consecutive_skips` skipped frames, so slow drift and new objects cannot hide.
    """

    def __init__(self, threshold: float, max_consecutive_skips: int = 10) -> None:
        self.threshold = threshold
        self.max_consecutive_skips = max_consecutive_skips
        self._last: np.ndarray | None = None
        self._skips = 0
        self.processed = 0
        self.skipped = 0

    def should_process(self, thumb: np.ndarray) -> bool:
        if self._last is None or self.threshold <= 0:
            return self._accept(thumb)
        diff = float(np.mean(np.abs(thumb.astype(np.int16) - self._last.astype(np.int16))))
        if diff >= self.threshold or self._skips >= self.max_consecutive_skips:
            return self._accept(thumb)
        self._skips += 1
        self.skipped += 1
        return False

    def _accept(self, thumb: np.ndarray) -> bool:
        self._last = thumb
        self._skips = 0
        self.processed += 1
        return True
