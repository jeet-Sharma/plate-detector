# Offline Plate Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (run natively in this session; the user asked for direct implementation).

**Goal:** Build the offline, CPU-only dashcam plate reader described in the spec.

**Architecture:** PyAV streaming decoder, scene-change gate, tiled YOLOv9-t detector (ONNX), full-resolution crops, fast-plate-ocr CCT-S (ONNX) giving per-slot probabilities, IoU tracker with probability voting, California format scoring, JSON/CSV output with measured time and peak memory.

**Tech Stack:** Python 3.11+, PyAV, NumPy, OpenCV (headless), ONNX Runtime, psutil, PyYAML, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-11-plate-recognition-design.md`

## Global Constraints

- Peak RAM at or below 512 MB in every preset (working set); peak commit also reported.
- No torch, TensorFlow, Ultralytics, `fast_plate_ocr` or `open_image_models` imported at runtime.
- No network access at runtime; models load from `models/`.
- ECC Python rules: PEP 8, type hints on all signatures, frozen dataclasses, `logging` not `print`, ruff clean.
- `anpr/plate_format.py` and `anpr/tracker.py` import neither cv2 nor onnxruntime.
- Tune on clip 1 only; clip 2 is the held-out clip.

## Review Focus

- Video with no detections: empty list, valid JSON/CSV, exit code 0.
- Plate text of unusual length (vanity plates, 2-8 chars): kept, not crashed on.
- Same plate in two tracks with a one-character misread: merged into one entry.
- Video path missing, or model file missing: clear message, non-zero exit.
- A frame with a plate box touching the frame edge: crop clamped, no empty crop.

## Tasks

### Task 1: Scaffolding, config, types
- Files: `pyproject.toml`, `requirements*.txt`, `.gitignore`, `anpr/{__init__,config,types,runtime}.py`, `tests/test_config.py`
- Produces: `Config` (frozen), `PRESETS`, `build_config(preset, **overrides)`, `Box`, `Detection`, `Reading`, `PlateResult`, `PlateAlphabet`.

### Task 2: Plate format (TDD)
- Files: `anpr/plate_format.py`, `tests/test_plate_format.py`
- Produces: `normalize`, `match_format`, `resolve(probs, alphabet, length)`, `edit_distance`.

### Task 3: Tracker (TDD)
- Files: `anpr/tracker.py`, `tests/test_tracker.py`
- Produces: `Tracker(config, alphabet)` with `update`, `touch`, `finalize`.

### Task 4: Video, gate, metrics, output
- Files: `anpr/{video,gate,metrics,output}.py`, tests for gate, output and timestamp formatting.

### Task 5: Models, detector, crop, OCR
- Files: `scripts/setup_models.py`, `anpr/{ort_utils,detector,crop,ocr}.py`, tests skipped when models are missing.

### Task 6: Pipeline and CLI
- Files: `anpr/pipeline.py`, `main.py`, smoke test.

### Task 7: Evaluation, tuning on clip 1, held-out check on clip 2, README
- Files: `scripts/{evaluate,benchmark}.py`, `eval/ground_truth/*.json`, `README.md`, rewrite `CLAUDE.md`.
