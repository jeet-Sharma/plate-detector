"""Plate detector: YOLOv9-t (end-to-end ONNX, NMS inside the graph).

Model contract (checked against the open-image-models source):
  input  ``images``  float32 (1, 3, 640, 640), RGB, values 0..1
  output ``output0`` float32 (N, 7), rows [batch, x1, y1, x2, y2, class, score] in input pixels
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from anpr.ort_utils import make_session
from anpr.types import Box, Detection

PAD_VALUE = 114


def tile_positions(length: int, tile: int, overlap: int) -> list[int]:
    """Start offsets that cover `length` with tiles of size `tile` overlapping by >= `overlap`."""
    if length <= tile:
        return [0]
    count = math.ceil((length - overlap) / (tile - overlap))
    stride = (length - tile) / (count - 1)
    return [round(i * stride) for i in range(count)]


def suppress_duplicates(
    detections: list[Detection], iou_threshold: float = 0.5, contain_threshold: float = 0.7
) -> list[Detection]:
    """Greedy suppression across tiles. A box mostly inside a better one is a cut-off copy."""
    kept: list[Detection] = []
    for det in sorted(detections, key=lambda d: d.score, reverse=True):
        if all(
            det.box.iou(k.box) < iou_threshold
            and det.box.intersection_over_min(k.box) < contain_threshold
            for k in kept
        ):
            kept.append(det)
    return kept


class PlateDetector:
    def __init__(
        self,
        model_path: Path,
        threads: int,
        conf: float = 0.3,
        mode: str = "tiled",
        det_width: int = 1920,
        tile_size: int = 640,
        tile_overlap: int = 96,
        min_plate_width: int = 40,
        ignore_bottom: float = 0.05,
        ignore_top: float = 0.0,
        min_aspect: float = 0.0,
        max_aspect: float = 1000.0,
        arena: bool = True,
    ) -> None:
        if mode not in ("tiled", "full"):
            raise ValueError("mode must be 'tiled' or 'full'")
        self.session = make_session(model_path, threads, arena)
        self.min_aspect = min_aspect
        self.max_aspect = max_aspect
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = int(self.session.get_inputs()[0].shape[2])
        self.conf = conf
        self.mode = mode
        self.det_width = det_width
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap
        self.min_plate_width = min_plate_width
        self.ignore_bottom = ignore_bottom
        self.ignore_top = ignore_top
        self.tiles_run = 0

    # ---- public ------------------------------------------------------------------

    def detect(self, frame: np.ndarray, deep: bool = True) -> list[Detection]:
        """Detect plates in a BGR frame. Boxes are in full-resolution pixel coordinates.

        `deep` selects the tiled pass (finds smaller, more distant plates, costs several
        model runs). Otherwise, or in "full" mode, one letterboxed pass covers the frame.
        """
        height, width = frame.shape[:2]
        if self.mode == "full" or not deep:
            raw = self._detect_full(frame)
        else:
            raw = self._detect_tiled(frame)
        raw = suppress_duplicates(raw)
        return [d for d in raw if self._keep(d.box, width, height)]

    # ---- internals ---------------------------------------------------------------

    def _keep(self, box: Box, width: int, height: int) -> bool:
        if box.width < self.min_plate_width or box.height <= 0:
            return False
        if not self.min_aspect <= box.width / box.height <= self.max_aspect:
            return False
        centre_y = box.center[1]
        return height * self.ignore_top <= centre_y <= height * (1.0 - self.ignore_bottom)

    def _run(self, square: np.ndarray) -> np.ndarray:
        """Run one input-sized BGR image; returns (N, 7) rows in input pixels."""
        blob = cv2.dnn.blobFromImage(square, 1.0 / 255.0, swapRB=True)
        self.tiles_run += 1
        return self.session.run(None, {self.input_name: blob})[0]

    def _canvas(self, image: np.ndarray) -> np.ndarray:
        size = self.input_size
        if image.shape[0] == size and image.shape[1] == size:
            return image
        canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
        canvas[: image.shape[0], : image.shape[1]] = image
        return canvas

    def _rows_to_detections(
        self, rows: np.ndarray, offset_x: float, offset_y: float, scale: float
    ) -> list[Detection]:
        out = []
        for _, x1, y1, x2, y2, _cls, score in rows:
            if score < self.conf:
                continue
            out.append(
                Detection(
                    Box(
                        (x1 + offset_x) / scale,
                        (y1 + offset_y) / scale,
                        (x2 + offset_x) / scale,
                        (y2 + offset_y) / scale,
                    ),
                    float(score),
                )
            )
        return out

    def _row_starts(self, height: int) -> list[int]:
        """Tile row offsets covering only the band between the ignored top and bottom strips."""
        size = self.input_size
        top = round(height * self.ignore_top)
        bottom = round(height * (1.0 - self.ignore_bottom))
        band = bottom - top
        if band >= size:
            return [top + y for y in tile_positions(band, size, self.tile_overlap)]
        # Band fits in one tile: centre the tile on the band, kept inside the image.
        centred = top - (size - band) // 2
        return [max(0, min(centred, height - size))] if height >= size else [0]

    def _detect_full(self, frame: np.ndarray) -> list[Detection]:
        """One letterboxed pass over the whole frame (low-memory preset)."""
        height, width = frame.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        new_w, new_h = round(width * scale), round(height * scale)
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        pad_x, pad_y = (self.input_size - new_w) / 2, (self.input_size - new_h) / 2
        canvas = np.full((self.input_size, self.input_size, 3), PAD_VALUE, dtype=np.uint8)
        canvas[round(pad_y) : round(pad_y) + new_h, round(pad_x) : round(pad_x) + new_w] = resized
        rows = self._run(canvas)
        return self._rows_to_detections(rows, -round(pad_x), -round(pad_y), scale)

    def _detect_tiled(self, frame: np.ndarray) -> list[Detection]:
        height, width = frame.shape[:2]
        scale = min(1.0, self.det_width / width)
        if scale < 1.0:
            small = cv2.resize(
                frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
            )
        else:
            small = frame
        sh, sw = small.shape[:2]
        size = self.input_size
        found: list[Detection] = []
        for y in self._row_starts(sh):
            for x in tile_positions(sw, size, self.tile_overlap):
                tile = self._canvas(small[y : y + size, x : x + size])
                found.extend(self._rows_to_detections(self._run(tile), x, y, scale))
        return found
