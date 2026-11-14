"""All tunable settings in one frozen dataclass, plus the three presets."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from anpr.runtime import physical_cores

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DETECTOR_FILE = "yolo-v9-t-640-license-plates-end2end.onnx"
OCR_FILE = "cct_s_v2_global.onnx"
OCR_CONFIG_FILE = "cct_s_v2_global_plate_config.yaml"


@dataclass(frozen=True)
class Config:
    video_path: Path
    out_dir: Path = Path("output")
    preset: str = "balanced"

    # Decoding and sampling
    sample_fps: float = 5.0
    decode_threads: int = 4
    threads: int = 4  # ONNX Runtime intra-op threads
    static_threshold: float = 2.0  # mean abs diff (0-255) below which a frame is skipped
    max_consecutive_skips: int = 10
    prefetch_depth: int = 1  # decoded frames kept ready by the decoder thread (about 12 MB each)
    max_seconds: float = 0.0  # stop after this many seconds of video (0 = whole video)

    ort_arena: bool = False  # on: detector about 28% faster, about 55 MB more memory

    # Detection
    det_mode: str = "tiled"  # "tiled" or "full" (single pass over the whole frame)
    deep_every: int = 3  # tiled mode: run the 3-tile pass on every Nth processed frame
    det_conf: float = 0.3
    min_aspect: float = 0.9  # plate width / height; a tilted plate can look nearly square
    max_aspect: float = 2.8  # real plates are about 2.0; wide badge lettering is 3.3 and up
    det_width: int = 1280  # tiled mode: frame is shrunk to this width first
    tile_size: int = 640
    tile_overlap: int = 96
    min_plate_width: int = 40  # pixels at full resolution
    ignore_bottom: float = 0.05  # fraction of frame height (camera overlay strip)
    ignore_top: float = 0.15  # fraction of frame height (sky, no plates)

    # Crop, OCR and text
    crop_padding: float = 0.08
    enhance: bool = True
    min_chars: int = 3

    # Tracking and output
    track_iou: float = 0.2
    track_max_age: int = 3
    keep_best: int = 15
    merge_ratio: float = 0.5
    merge_gap_s: float = 2.0
    # Chosen from clip 1's unfiltered tracks: every readable plate there scores 0.39 or more,
    # while single-frame noise stays at or below 0.40 and almost always below 0.30.
    min_confidence: float = 0.35
    min_readings: int = 2
    high_confidence: float = 0.65  # a single very confident reading is also accepted

    debug: bool = False
    models_dir: Path = MODELS_DIR


PRESETS: dict[str, dict[str, Any]] = {
    "low-mem": {
        "decode_threads": 1, "threads": 1, "sample_fps": 3.0, "det_mode": "full",
        "ort_arena": False,
    },
    "balanced": {
        "decode_threads": 4, "threads": 4, "sample_fps": 5.0, "det_mode": "tiled", "deep_every": 3,
    },
    "fast": {
        "decode_threads": 4, "threads": 4, "sample_fps": 4.0, "det_mode": "tiled", "deep_every": 4,
        "enhance": False,
    },
}


def build_config(video_path: str | Path, preset: str = "balanced", **overrides: Any) -> Config:
    """Preset values first, then explicit overrides (``None`` overrides are ignored).

    Thread counts are capped at the machine's physical core count.
    """
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    valid = {f.name for f in fields(Config)}
    unknown = set(overrides) - valid
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")

    values = {**PRESETS[preset], **{k: v for k, v in overrides.items() if v is not None}}
    cap = physical_cores()
    for key in ("decode_threads", "threads"):
        if key in values:
            values[key] = max(1, min(int(values[key]), cap))
    base = Config(video_path=Path(video_path), preset=preset)
    return replace(base, **values)
