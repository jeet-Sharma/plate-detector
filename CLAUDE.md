# CLAUDE.md

Guidance for Claude Code in this repository. Requirements: [REQUIREMENTS.md](REQUIREMENTS.md).
Design: [docs/superpowers/specs/2026-11-11-plate-recognition-design.md](docs/superpowers/specs/2026-11-11-plate-recognition-design.md).
Where this file and the design disagree, this file reflects what was built and measured.

## What this is

Offline, CPU-only tool: `python main.py <video>` lists the unique number plates in a dashcam
video (JSON, CSV, console) and reports processing time and peak memory. Budget: 512 MB RAM.
The supplied clips are 4K HEVC, 30 FPS, 60 s, with **California** plates.

## Hard rules (enforced by `tests/test_hard_rules.py`)

1. No torch, TensorFlow, Ultralytics, `fast_plate_ocr` or `open_image_models` at runtime.
   Models are plain ONNX files run with ONNX Runtime.
2. No network at runtime. Models come from `models/` (`python scripts/setup_models.py`, once).
3. Peak RAM at or below 512 MB in every preset. Measure it; never assume.
4. `anpr/plate_format.py`, `anpr/tracker.py`, `anpr/types.py` import no cv2, onnxruntime or av.
5. `anpr/__init__.py` and `anpr/runtime.py` stay light: `main.py` sets BLAS thread env vars
   before numpy loads (otherwise numpy commits about 500 MB on Windows).
6. Scope: plate recognition only.

## Pipeline and modules

```
PyAV decode (thread) -> change gate -> detector -> crop (full-res) -> OCR -> tracker -> output
```

| Module | Job |
|---|---|
| `video.py` | PyAV reader, decoder threads capped, `prefetch()` decodes in a background thread |
| `gate.py` | Skips unchanged frames (160x90 thumbnail difference); tracks get `touch()` |
| `detector.py` | YOLOv9-t ONNX. Tiled pass (1280 px wide, 3 tiles) every Nth frame, single letterboxed pass otherwise. Filters: width, aspect 0.9 to 2.8, ignore top 15% and bottom 5% |
| `crop.py` | Crop from the full 4K frame, white balance + CLAHE variant, OCR input |
| `ocr.py` | fast-plate-ocr CCT-S ONNX; returns per-slot probabilities (10 x 37) |
| `plate_format.py` | Normalise, templates, likelihood-based fixes (see below) |
| `tracker.py` | IoU tracking, probability voting, post-merge of near duplicates |
| `metrics.py` | Stage timers, peak memory (sampled + OS counter) |
| `pipeline.py` | Wires it together, writes `<stem>.plates.json/.csv` |
| `config.py` | One frozen `Config`, three presets, `build_config()` |

## Live UI (`ui/`, optional)

- `python -m ui` serves a page on 127.0.0.1. Standard library only; the page uses no external
  addresses (tests enforce both). `anpr/` never imports `ui/`.
- `ui/growing_file.py`: reader over an upload still arriving. An MP4 with `moov` before `mdat`
  reports "not seekable" while incomplete, so FFmpeg decodes straight through instead of
  jumping to the end of the file (which would wait for the whole upload).
- `ui/jobs.py`: one job at a time; append-only event log; only the newest frame is kept.
- `anpr/live.py` builds the events (`frame`, `reading`, `plates`, `stats`, `progress`);
  `pipeline.run(..., observer=, cancel=, source_factory=)` is the only hook. Event payloads
  must be plain JSON: a numpy float in a frame event once froze the stream.
- A run whose upload dies must never hold the single slot: `Job.stalled()` plus a watchdog end
  it after 45 s, a new request may replace one idle for 15 s, the page cancels on `pagehide`
  and on a hard upload failure, and chunks are idempotent (retry resumes from `received`).
  `scripts/ui_resilience_check.py` drives these failures in Chrome.
- Design record: `docs/superpowers/specs/2026-11-14-live-ui-design.md`.

## Decisions that came from measurement (do not undo without re-measuring)

- **Decoder threads set the memory floor.** About 250 MB base plus about 50 MB per decoder
  thread. Auto threading peaks at 624 MB. Default is 4.
- **ONNX arena off by default.** On: detector faster, but clip 2 peaked at 517 MB.
  Off: 371 MB. `--arena` turns it on.
- **Decoding is the bottleneck** (about 44 s per clip at 4 threads), so decode runs in a
  producer thread and overlaps with detection.
- **Single-pass detection finds plates of about 80 px and wider.** The tiled pass finds smaller
  ones but costs 3 model runs, so it runs on every 3rd processed frame.
- **Format fixes never overrule a confident model.** A template-conforming rewrite is accepted
  only if it costs at most `log(3)` nats of probability; look-alike pairs (0/O, 1/I, 8/B, 5/S,
  2/Z, 6/G) cost at most 0.3. Forcing `8D94661` (a California motorcycle plate) into the car
  format gave a wrong answer in testing.
- **Badge lettering** ("CHEVROLET", "MAVERICK") is rejected by plate shape (aspect over 2.8),
  not by text rules: `EABLEB` is a real all-letter vanity plate.
- **Formats** (`plate_format.TEMPLATES`): `1ABC234`, motorcycle `1A23456`, older `ABC123`, `123ABC`.
  Anything else is kept with weight 0.75.
- **Output filter:** confidence at least 0.35 and (readings at least 2 or confidence at least
  0.65). Chosen from clip 1's unfiltered tracks; clip 2 was seen while labelling, so it is not
  a clean held-out set.

## Commands

```bash
python -m venv .venv && .venv/Scripts/activate        # source .venv/bin/activate on Linux
pip install -r requirements.txt                        # runtime
pip install -r requirements-dev.txt                    # tests, lint, onnx
python scripts/setup_models.py                         # one-time download, checks SHA-256
python main.py REC20261007-174724-7378.mp4             # presets: low-mem, balanced, fast
python main.py <video> --debug --min-confidence 0 --min-readings 1   # inspect every track
python -m ui                                           # live web UI on http://127.0.0.1:8765
python scripts/ui_screenshots.py                       # drive the UI in Chrome, save docs/ui/*.png
pytest -q                                              # 137 tests, about 40 s
ruff check .
python scripts/evaluate.py output/<clip>.plates.json eval/ground_truth/<clip>.json
python scripts/benchmark.py                            # all presets on all clips
```

## Conventions

- Python 3.11+, type hints on every signature, frozen dataclasses for records, `logging` not
  `print` (the results table in `main.py` is the exception), ruff clean (line length 100).
- Tests: `pytest.mark.unit` needs nothing; `pytest.mark.integration` needs the models and
  skips itself when they are missing.
- Do not commit videos, model files or `.venv/` (see `.gitignore`).
- Ground truth in `eval/ground_truth/` is a draft until a person has checked it against the video.

## Open items

- `9VCA051` (a turning car, visible about 1 s) is missed.
- The INT8 detector from the design is not built. Detection is not the bottleneck.
- A person should confirm the ground truth, especially `9XTN530` (X or Y) and `9ECF543` (E or F).
