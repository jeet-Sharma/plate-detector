"""Live progress events for a front end (the optional web UI).

``LiveReporter`` turns pipeline state into small JSON-ready events and hands them to an
observer callback. ``NullReporter`` does nothing, so the command-line path pays no cost.

Events (all plain dicts):
  start     video facts and settings
  frame     one analysed frame as a 960 px JPEG plus the plate boxes found in it
  progress  a skipped (unchanged) frame, only to keep the timeline moving
  reading   one OCR reading with its crop, text and per-character probabilities
  plates    the current accepted plates (same vote and filter as the final result)
  stats     rolling latency per component, memory, frames analysed and skipped
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from anpr.metrics import StageTimers, current_rss_mb
from anpr.types import PlateAlphabet, PlateResult, Reading

Observer = Callable[[str, dict[str, Any]], None]

FRAME_WIDTH = 960
FRAME_QUALITY = 70
CROP_WIDTH = 128
CROP_QUALITY = 72
PLATES_EVERY_S = 0.35
STATS_EVERY_S = 1.0
PROGRESS_EVERY_S = 0.25


@dataclass(frozen=True)
class LiveItem:
    """One OCR reading, its crop and the track it joined."""

    reading: Reading
    crop: np.ndarray
    track_id: int


def _jpeg_b64(image: np.ndarray, quality: int) -> str:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def _mean_ms(samples: list[float], tail: int = 20) -> float:
    recent = samples[-tail:]
    return round(1000 * sum(recent) / len(recent), 1) if recent else 0.0


class NullReporter:
    """Same interface as LiveReporter; every call is a no-op."""

    active = False

    def start(self, *args: Any, **kwargs: Any) -> None: ...
    def frame(self, *args: Any, **kwargs: Any) -> None: ...
    def skipped(self, *args: Any, **kwargs: Any) -> None: ...
    def plates_due(self, force: bool = False) -> bool:
        return False

    def plates(self, *args: Any, **kwargs: Any) -> None: ...
    def stats(self, *args: Any, **kwargs: Any) -> None: ...


class LiveReporter:
    active = True

    def __init__(
        self,
        observer: Observer,
        alphabet: PlateAlphabet,
        timers: StageTimers,
        decode_times: list[float],
        duration: float,
    ) -> None:
        self._emit = observer
        self._alphabet = alphabet
        self._timers = timers
        self._decode_times = decode_times
        self._duration = duration
        self._began = time.perf_counter()
        self._last_plates = 0.0
        self._last_stats = 0.0
        self._last_progress = 0.0

    def _progress(self, timestamp: float) -> float:
        if self._duration <= 0:
            return 0.0
        return round(min(1.0, max(0.0, timestamp / self._duration)), 4)

    def start(self, info: dict[str, Any], preset: str, sample_fps: float) -> None:
        self._emit("start", {"video": info, "preset": preset, "sample_fps": sample_fps})

    def frame(
        self,
        image: np.ndarray,
        timestamp: float,
        items: list[LiveItem],
        kind: str,
        frame_ms: float,
    ) -> None:
        height, width = image.shape[:2]
        scale = FRAME_WIDTH / width
        small = cv2.resize(
            image, (FRAME_WIDTH, max(1, round(height * scale))), interpolation=cv2.INTER_AREA
        )
        boxes = []
        for item in items:
            box = item.reading.box
            boxes.append(
                {
                    "id": item.track_id,
                    "x": round(float(box.x1) / width, 4),
                    "y": round(float(box.y1) / height, 4),
                    "w": round(float(box.width) / width, 4),
                    "h": round(float(box.height) / height, 4),
                    "text": self._alphabet.decode(item.reading.probs),
                    "conf": round(float(item.reading.confidence), 3),
                }
            )
        self._emit(
            "frame",
            {
                "t": round(timestamp, 3),
                "progress": self._progress(timestamp),
                "jpeg": _jpeg_b64(small, FRAME_QUALITY),
                "kind": kind,
                "frame_ms": round(frame_ms, 1),
                "boxes": boxes,
            },
        )
        for item in items:
            self._reading(item, timestamp)
        self._last_progress = time.perf_counter()

    def _reading(self, item: LiveItem, timestamp: float) -> None:
        crop = item.crop
        scale = CROP_WIDTH / max(1, crop.shape[1])
        thumb = cv2.resize(
            crop,
            (CROP_WIDTH, max(1, round(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
        )
        text = self._alphabet.decode(item.reading.probs)
        chars = [round(float(item.reading.probs[i].max()), 3) for i in range(len(text))]
        self._emit(
            "reading",
            {
                "id": item.track_id,
                "t": round(timestamp, 3),
                "text": text,
                "conf": round(float(item.reading.confidence), 3),
                "chars": chars,
                "crop": _jpeg_b64(thumb, CROP_QUALITY),
            },
        )

    def skipped(self, timestamp: float, skipped_total: int) -> None:
        now = time.perf_counter()
        if now - self._last_progress >= PROGRESS_EVERY_S:
            self._last_progress = now
            self._emit(
                "progress",
                {"t": round(timestamp, 3), "progress": self._progress(timestamp),
                 "skipped": skipped_total},
            )

    def plates_due(self, force: bool = False) -> bool:
        return force or time.perf_counter() - self._last_plates >= PLATES_EVERY_S

    def plates(self, accepted: list[PlateResult]) -> None:
        self._last_plates = time.perf_counter()
        payload = [
            {
                "id": p.track_ids[0] if p.track_ids else -1,
                "ids": list(p.track_ids),
                "text": p.plate,
                "confidence": round(p.confidence, 3),
                "readings": p.readings,
                "first_seen": round(p.first_seen, 2),
                "last_seen": round(p.last_seen, 2),
                "format": p.matches_format,
                "chars": list(p.char_confidence),
            }
            for p in accepted
        ]
        self._emit("plates", {"plates": payload})

    def stats(
        self, timestamp: float, sampled: int, processed: int, skipped: int, force: bool = False
    ) -> None:
        now = time.perf_counter()
        if not force and now - self._last_stats < STATS_EVERY_S:
            return
        self._last_stats = now
        s = self._timers.samples
        detect = s.get("detect_single", [])[-10:] + s.get("detect_tiled", [])[-10:]
        elapsed = now - self._began
        self._emit(
            "stats",
            {
                "latency_ms": {
                    "decode": _mean_ms(self._decode_times, 60),
                    "gate": _mean_ms(s.get("gate", [])),
                    "convert": _mean_ms(s.get("convert", [])),
                    "detect": _mean_ms(detect, 20),
                    "ocr": _mean_ms(s.get("ocr", [])),
                    "track": _mean_ms(s.get("track", [])),
                    "frame": _mean_ms(s.get("frame_total", [])),
                },
                "frames": {"sampled": sampled, "processed": processed, "skipped": skipped},
                "memory_mb": current_rss_mb(),
                "elapsed_s": round(elapsed, 1),
                "rtf": round(elapsed / timestamp, 2) if timestamp > 0.5 else None,
            },
        )
