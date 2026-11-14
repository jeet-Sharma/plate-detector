"""Run every preset on every clip and write a results table.

    python scripts/benchmark.py [--presets balanced fast low-mem] [video ...]

Each run is a separate process, so peak memory is per run. The parent also samples the
child's resident memory from outside (independent of the program's own report).
Results go to output/benchmark/results.json and output/benchmark/results.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.evaluate import evaluate  # noqa: E402

MB = 1024 * 1024
DEFAULT_VIDEOS = sorted(ROOT.glob("REC*.mp4"))


def run_once(video: Path, preset: str, out_dir: Path) -> dict:
    cmd = [sys.executable, str(ROOT / "main.py"), str(video), "--preset", preset,
           "--out-dir", str(out_dir)]
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    watcher = psutil.Process(proc.pid)
    external_peak = 0
    while proc.poll() is None:
        try:
            # On Windows the venv's python.exe is a launcher that starts the real interpreter
            # as a child, so add up the whole process tree.
            tree = [watcher, *watcher.children(recursive=True)]
            total = sum(p.memory_info().rss for p in tree if p.is_running())
            external_peak = max(external_peak, total)
        except psutil.Error:
            pass
        time.sleep(0.05)
    wall = time.perf_counter() - started
    result_path = out_dir / f"{video.stem}.plates.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    entry = {
        "video": video.name,
        "preset": preset,
        "exit_code": proc.returncode,
        "wall_s": round(wall, 1),
        "internal_total_s": result["run"]["timing_s"]["total"],
        "stage_s": result["run"]["timing_s"],
        "peak_rss_mb_internal": result["run"]["memory"]["peak_rss_mb"],
        "peak_rss_mb_external": round(external_peak / MB, 1),
        "peak_private_mb": result["run"]["memory"].get("peak_private_mb"),
        "frames": result["run"]["frames"],
        "real_time_factor": result["run"]["throughput"]["real_time_factor"],
        "latency_ms": result["run"]["latency_ms"],
        "plates": [p["plate"] for p in result["plates"]],
    }
    truth_path = ROOT / "eval" / "ground_truth" / f"{video.stem}.json"
    if truth_path.exists():
        report = evaluate(result, json.loads(truth_path.read_text(encoding="utf-8")))
        entry["recall"] = report["recall"]
        entry["precision"] = report["precision"]
        entry["missed"] = report["missed"]
        entry["false_positives"] = report["false_positives"]
        entry["duplicate_variants"] = report["duplicate_variants"]
        entry["character_error_rate"] = report["character_error_rate"]
    return entry


def pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


COMPONENTS = (
    ("decode_per_source_frame", "Decode, per source frame (30 fps video)"),
    ("gate", "Change gate"),
    ("convert", "Convert to full-resolution image"),
    ("detect_single", "Detector, single pass"),
    ("detect_tiled", "Detector, 3-tile pass"),
    ("crop", "Crop"),
    ("ocr", "OCR (all plates in the frame)"),
    ("track", "Tracker update"),
    ("frame_total", "Whole frame through the models"),
)


def to_markdown(rows: list[dict]) -> str:
    out = [
        "## Accuracy, time and memory",
        "",
        "| Clip | Preset | Plates found | Precision | Character accuracy | Wall time | "
        "Real-time factor | Peak RAM (self) | Peak RAM (outside) | Peak private |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cer = r.get("character_error_rate")
        out.append(
            f"| {r['video'][-8:-4]} | {r['preset']} | {pct(r.get('recall'))} | "
            f"{pct(r.get('precision'))} | {pct(None if cer is None else 1 - cer)} | "
            f"{r['wall_s']} s | {r['real_time_factor']}x | {r['peak_rss_mb_internal']} MB | "
            f"{r['peak_rss_mb_external']} MB | {r['peak_private_mb']} MB |"
        )
    out += ["", "## Latency per call, in milliseconds (mean / 95th percentile)", ""]
    labels = [f"{r['video'][-8:-4]} {r['preset']}" for r in rows]
    out.append("| Component | " + " | ".join(labels) + " |")
    out.append("|---|" + "---|" * len(rows))
    for key, label in COMPONENTS:
        cells = []
        for r in rows:
            stat = r["latency_ms"].get(key)
            cells.append(f"{stat['mean_ms']:.1f} / {stat['p95_ms']:.1f}" if stat else "-")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("videos", nargs="*", type=Path, default=DEFAULT_VIDEOS)
    parser.add_argument("--presets", nargs="+", default=["balanced", "fast", "low-mem"])
    args = parser.parse_args()

    out_root = ROOT / "output" / "benchmark"
    rows = []
    for preset in args.presets:
        for video in args.videos:
            print(f"running {preset} on {video.name} ...", flush=True)
            rows.append(run_once(video, preset, out_root / preset))
            r = rows[-1]
            print(f"  {r['wall_s']} s, {r['peak_rss_mb_external']} MB outside, "
                  f"recall {r.get('recall')}, precision {r.get('precision')}", flush=True)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (out_root / "results.md").write_text(to_markdown(rows), encoding="utf-8")
    print(to_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
