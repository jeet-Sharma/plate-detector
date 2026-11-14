"""Wires the stages together: decode -> gate -> detect -> crop -> OCR -> track -> output."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from anpr import __version__
from anpr.config import DETECTOR_FILE, OCR_CONFIG_FILE, OCR_FILE, Config
from anpr.crop import crop_plate, enhance, sharpness, to_ocr_input
from anpr.detector import PlateDetector
from anpr.gate import ChangeGate
from anpr.live import LiveItem, LiveReporter, NullReporter
from anpr.metrics import MemoryMonitor, StageTimers
from anpr.ocr import PlateOCR
from anpr.output import write_csv, write_json
from anpr.tracker import Tracker, TrackerConfig
from anpr.types import Detection, PlateResult, Reading
from anpr.video import VideoError, iter_sampled_frames, prefetch, probe

log = logging.getLogger(__name__)

MEMORY_BUDGET_MB = 512


class Cancelled(RuntimeError):
    """The caller asked the run to stop (see the `cancel` argument of `run`)."""


@dataclass(frozen=True)
class RunSummary:
    plates: list[PlateResult]
    run: dict[str, Any]
    json_path: Path
    csv_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sharpness_factor(value: float) -> float:
    """0.1 (blurry) to 1.0 (sharp). Laplacian variance of 50 gives about 0.55."""
    return 0.1 + 0.9 * value / (value + 50.0)


def _keep_plate(result: PlateResult, config: Config) -> bool:
    if result.confidence < config.min_confidence:
        return False
    return result.readings >= config.min_readings or result.confidence >= config.high_confidence


def run(
    config: Config,
    *,
    source_factory: Callable[[], Any] | None = None,
    observer: Callable[[str, dict[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
) -> RunSummary:
    """Process one video and write the JSON and CSV files.

    `source_factory` (optional) returns a fresh file-like reader each time it is called, for
    video that is still arriving. `observer` (optional) receives live events, see
    ``anpr.live``. `cancel` (optional) stops the run with ``Cancelled`` when set.
    """
    started = time.perf_counter()
    video = Path(config.video_path)
    if source_factory is None and not video.exists():
        raise VideoError(f"video not found: {video}")

    def open_source() -> Any:
        return source_factory() if source_factory is not None else video

    info = probe(open_source())
    log.info(
        "%s: %dx%d %.1f fps %.1f s (%s)",
        video.name, info.width, info.height, info.fps, info.duration, info.codec,
    )

    timers = StageTimers()
    cv2.setNumThreads(config.threads)
    with MemoryMonitor() as memory:
        with timers.stage("load_models"):
            detector = PlateDetector(
                config.models_dir / DETECTOR_FILE,
                threads=config.threads,
                conf=config.det_conf,
                mode=config.det_mode,
                det_width=config.det_width,
                tile_size=config.tile_size,
                tile_overlap=config.tile_overlap,
                min_plate_width=config.min_plate_width,
                ignore_bottom=config.ignore_bottom,
                ignore_top=config.ignore_top,
                min_aspect=config.min_aspect,
                max_aspect=config.max_aspect,
                arena=config.ort_arena,
            )
            ocr = PlateOCR(
                config.models_dir / OCR_FILE,
                config.models_dir / OCR_CONFIG_FILE,
                config.threads,
                config.ort_arena,
            )
        tracker = Tracker(
            TrackerConfig(
                alphabet=ocr.alphabet,
                iou_threshold=config.track_iou,
                max_age=config.track_max_age,
                keep_best=config.keep_best,
                merge_ratio=config.merge_ratio,
                merge_gap_s=config.merge_gap_s,
            )
        )
        gate = ChangeGate(config.static_threshold, config.max_consecutive_skips)
        debug_dir = _prepare_debug_dir(config)

        sampled = detections = readings_total = 0
        decode_times: list[float] = []  # decode time of every source frame, in seconds
        reporter: LiveReporter | NullReporter = (
            LiveReporter(observer, ocr.alphabet, timers, decode_times, info.duration)
            if observer is not None
            else NullReporter()
        )
        reporter.start(
            {
                "name": video.name, "width": info.width, "height": info.height,
                "fps": info.fps, "duration_s": round(info.duration, 2), "codec": info.codec,
            },
            config.preset,
            config.sample_fps,
        )
        frames = timers.timed_iter(
            "decode_wait",
            prefetch(
                iter_sampled_frames(
                    open_source(),
                    config.sample_fps,
                    config.decode_threads,
                    config.max_seconds,
                    decode_times,
                ),
                depth=config.prefetch_depth,
            ),
        )
        deep_every = max(1, config.deep_every)
        for sf in frames:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            sampled += 1
            with timers.stage("gate"):
                process = gate.should_process(sf.thumb)
            if not process:
                tracker.touch(sf.timestamp)
                reporter.skipped(sf.timestamp, gate.skipped)
                reporter.stats(sf.timestamp, sampled, gate.processed, gate.skipped)
                continue
            frame_started = time.perf_counter()
            with timers.stage("convert"):
                image = sf.bgr()
            deep = config.det_mode == "tiled" and (gate.processed - 1) % deep_every == 0
            with timers.stage("detect_tiled" if deep else "detect_single"):
                found = detector.detect(image, deep=deep)
            detections += len(found)
            with timers.stage("crop"):
                pairs = []
                for det in found:
                    crop = crop_plate(image, det.box, config.crop_padding)
                    if crop is not None:
                        pairs.append((det, crop))
            with timers.stage("ocr"):
                read = _read_plates(pairs, sf.timestamp, ocr, config, debug_dir)
            readings = [reading for reading, _ in read]
            readings_total += len(readings)
            with timers.stage("track"):
                track_ids = tracker.update(sf.timestamp, readings)
            frame_seconds = time.perf_counter() - frame_started
            timers.record("frame_total", frame_seconds)
            if reporter.active:
                items = [
                    LiveItem(reading, crop, tid)
                    for (reading, crop), tid in zip(read, track_ids, strict=True)
                ]
                reporter.frame(
                    image, sf.timestamp, items, "tiled" if deep else "single", 1000 * frame_seconds
                )
                if reporter.plates_due(force=False):
                    reporter.plates([p for p in tracker.snapshot() if _keep_plate(p, config)])
                reporter.stats(sf.timestamp, sampled, gate.processed, gate.skipped)
            del image

        with timers.stage("merge"):
            all_results = tracker.finalize()
        plates = [p for p in all_results if _keep_plate(p, config)]
        reporter.plates(plates)
        reporter.stats(info.duration, sampled, gate.processed, gate.skipped, force=True)

    wall = time.perf_counter() - started
    mem = memory.report()
    if mem["peak_rss_mb"] and float(mem["peak_rss_mb"]) > MEMORY_BUDGET_MB:
        log.warning(
            "peak memory %.0f MB exceeds the %d MB budget", mem["peak_rss_mb"], MEMORY_BUDGET_MB
        )

    run_info = {
        "version": __version__,
        "video": {
            "path": str(video), "width": info.width, "height": info.height,
            "fps": info.fps, "duration_s": round(info.duration, 2), "codec": info.codec,
        },
        "preset": config.preset,
        "settings": {
            k: str(v) if isinstance(v, Path) else v for k, v in asdict(config).items()
        },
        "models": {
            name: {"file": name, "sha256": _sha256(config.models_dir / name)}
            for name in (DETECTOR_FILE, OCR_FILE)
        },
        "frames": {
            "sampled": sampled,
            "processed": gate.processed,
            "skipped_by_gate": gate.skipped,
            "detector_passes": detector.tiles_run,
            "detections": detections,
            "readings": readings_total,
        },
        "timing_s": {
            "total": round(wall, 2),
            **{k: round(v, 2) for k, v in timers.seconds.items()},
        },
        "memory": {**mem, "budget_mb": MEMORY_BUDGET_MB},
        "latency_ms": timers.latency_ms({"decode_per_source_frame": decode_times}),
        "throughput": _throughput(info.duration, wall, len(decode_times), gate.processed),
        "tracks_before_filter": len(all_results),
    }

    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{video.stem}.plates.json"
    csv_path = out_dir / f"{video.stem}.plates.csv"
    write_json(json_path, plates, run_info)
    write_csv(csv_path, plates)
    log.info("wrote %s and %s", json_path.name, csv_path.name)
    return RunSummary(plates, run_info, json_path, csv_path)


def _throughput(
    video_seconds: float, wall: float, source_frames: int, processed: int
) -> dict[str, float]:
    """How fast the run was compared with the video's own clock."""
    wall = max(wall, 1e-9)
    return {
        "video_seconds": round(video_seconds, 2),
        "processing_seconds": round(wall, 2),
        "real_time_factor": round(wall / video_seconds, 2) if video_seconds > 0 else 0.0,
        "source_frames_decoded": source_frames,
        "decode_fps_average": round(source_frames / wall, 1),
        "frames_through_models": processed,
        "frames_through_models_per_s": round(processed / wall, 2),
    }


def _read_plates(
    pairs: list[tuple[Detection, np.ndarray]],
    timestamp: float,
    ocr: PlateOCR,
    config: Config,
    debug_dir: Path | None,
) -> list[tuple[Reading, np.ndarray]]:
    """OCR every crop (raw and, optionally, enhanced) and average the slot probabilities.

    Returns each accepted reading together with its crop."""
    if not pairs:
        return []
    variants_per_crop = 2 if config.enhance else 1
    inputs = []
    for _, crop in pairs:
        inputs.append(to_ocr_input(crop, ocr.height, ocr.width))
        if config.enhance:
            inputs.append(to_ocr_input(enhance(crop), ocr.height, ocr.width))
    probs = ocr.read(inputs)

    readings: list[tuple[Reading, np.ndarray]] = []
    for i, (det, crop) in enumerate(pairs):
        mean_probs = np.mean(probs[i * variants_per_crop : (i + 1) * variants_per_crop], axis=0)
        text = ocr.alphabet.decode(mean_probs)
        if len(text) < config.min_chars:
            continue
        ocr_conf = float(np.mean(mean_probs.max(axis=1)[: len(text)]))
        confidence = det.score * ocr_conf
        weight = confidence * _sharpness_factor(sharpness(crop))
        readings.append((Reading(timestamp, det.box, mean_probs, confidence, weight), crop))
        if debug_dir is not None:
            name = f"{timestamp:07.3f}_{text}_{confidence:.2f}_{i}.png"
            cv2.imwrite(str(debug_dir / name), crop)
    return readings


def _prepare_debug_dir(config: Config) -> Path | None:
    if not config.debug:
        return None
    path = Path(config.out_dir) / "debug" / Path(config.video_path).stem
    path.mkdir(parents=True, exist_ok=True)
    return path


LATENCY_ORDER = (
    "decode_per_source_frame",
    "decode_wait",
    "gate",
    "convert",
    "detect_single",
    "detect_tiled",
    "crop",
    "ocr",
    "track",
    "frame_total",
)


def latency_table(latency: dict[str, dict]) -> str:
    """Per-call latency of each pipeline component, in milliseconds."""
    header = f"{'component':<26}{'calls':>6}{'mean':>9}{'median':>9}{'p95':>9}{'max':>9}"
    lines = [header, "-" * len(header)]
    for name in LATENCY_ORDER:
        if name in latency:
            s = latency[name]
            lines.append(
                f"{name:<26}{s['calls']:>6}{s['mean_ms']:>9.1f}{s['p50_ms']:>9.1f}"
                f"{s['p95_ms']:>9.1f}{s['max_ms']:>9.1f}"
            )
    return chr(10).join(lines)


def describe(summary: RunSummary) -> str:
    """Human summary printed after the plate table."""
    run = summary.run
    t, m, f, tp = run["timing_s"], run["memory"], run["frames"], run["throughput"]
    stages = ", ".join(f"{k} {v:.1f}s" for k, v in t.items() if k != "total")
    private = f", peak private {m['peak_private_mb']} MB" if m.get("peak_private_mb") else ""
    lines = [
        f"Time {t['total']:.1f}s for {tp['video_seconds']:.1f}s of video "
        f"(real-time factor {tp['real_time_factor']}x; 1.0 would be exactly real time)",
        f"Stage totals: {stages}",
        f"Peak memory {m['peak_rss_mb']} MB ({m['source']}){private}; budget {m['budget_mb']} MB",
        f"Frames sampled {f['sampled']}, through the models {f['processed']}, "
        f"skipped by the change gate {f['skipped_by_gate']}",
        "",
        "Latency per call (milliseconds)",
        latency_table(run["latency_ms"]),
    ]
    return chr(10).join(lines)
