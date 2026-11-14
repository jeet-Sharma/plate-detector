"""Command-line entry point: python main.py <video_path> [options]."""

from __future__ import annotations

import argparse
import logging
import sys

from anpr.runtime import apply_environment


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py", description="Find the unique number plates in a dashcam video (offline)."
    )
    p.add_argument("video_path", help="path to the video file")
    p.add_argument("--preset", choices=["low-mem", "balanced", "fast"], default="balanced")
    p.add_argument("--out-dir", default="output", help="where JSON and CSV files go")
    p.add_argument("--sample-fps", type=float, help="frames processed per second of video")
    p.add_argument("--decode-threads", type=int, help="video decoder threads")
    p.add_argument("--threads", type=int, help="ONNX Runtime threads")
    p.add_argument("--det-mode", choices=["tiled", "full"], help="detector layout")
    p.add_argument("--det-conf", type=float, help="detector score threshold")
    p.add_argument("--min-confidence", type=float, help="final plate confidence threshold")
    p.add_argument("--min-readings", type=int, help="frames a plate must be read in")
    p.add_argument("--min-plate-width", type=int, help="smallest plate width in pixels")
    p.add_argument("--ignore-bottom", type=float, help="bottom fraction of the frame to ignore")
    p.add_argument("--ignore-top", type=float, help="top fraction of the frame to ignore")
    p.add_argument("--static-threshold", type=float, help="scene-change gate (0 disables)")
    p.add_argument("--track-max-age", type=int, help="frames a plate may vanish before it ends")
    p.add_argument("--max-seconds", type=float, help="only process the first N seconds")
    p.add_argument("--arena", action="store_true", help="ONNX memory arena on (faster, more RAM)")
    p.add_argument("--no-arena", action="store_true", help="ONNX memory arena off (less RAM)")
    p.add_argument("--no-enhance", action="store_true", help="skip the enhanced OCR variant")
    p.add_argument("--debug", action="store_true", help="save plate crops to <out-dir>/debug")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    apply_environment(1)  # before numpy/onnxruntime are imported

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    from anpr.config import build_config
    from anpr.ort_utils import ModelMissingError
    from anpr.output import console_table
    from anpr.pipeline import describe, run
    from anpr.video import VideoError

    overrides = {
        "out_dir": args.out_dir,
        "sample_fps": args.sample_fps,
        "decode_threads": args.decode_threads,
        "threads": args.threads,
        "det_mode": args.det_mode,
        "det_conf": args.det_conf,
        "min_confidence": args.min_confidence,
        "min_readings": args.min_readings,
        "min_plate_width": args.min_plate_width,
        "ignore_bottom": args.ignore_bottom,
        "ignore_top": args.ignore_top,
        "static_threshold": args.static_threshold,
        "track_max_age": args.track_max_age,
        "max_seconds": args.max_seconds,
        "ort_arena": True if args.arena else (False if args.no_arena else None),
        "enhance": False if args.no_enhance else None,
        "debug": True if args.debug else None,
    }
    config = build_config(args.video_path, args.preset, **overrides)
    try:
        summary = run(config)
    except VideoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ModelMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print()
    print(console_table(summary.plates))
    print()
    print(describe(summary))
    print(f"Wrote {summary.json_path} and {summary.csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
