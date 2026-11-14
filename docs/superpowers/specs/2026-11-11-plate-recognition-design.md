# Design: Offline Dashcam Number Plate Recognition

Date: 2026-11-11
Inputs: [REQUIREMENTS.md](../../../REQUIREMENTS.md), the two supplied clips, measurements and web
research done on 2026-11-11 (summarised in "Evidence" at the end).

**Precedence:** this design supersedes the earlier draft decisions in CLAUDE.md (OpenCV decoding,
CLI flags, output names, confidence formula, layout). The first task of the implementation plan
is to rewrite CLAUDE.md to match this document. Until then, where they disagree, this document
wins. *(Design author's call.)*

## 1. Intent

This is an interview assignment. The interviewer runs `python main.py <video>` on two 4K dashcam
clips and judges it on four things:

1. **Efficiency:** careful use of memory and CPU, at or below 512 MB peak RAM.
2. **Speed:** how quickly a clip is processed.
3. **Accuracy:** every readable plate found, read correctly, listed once.
4. **Small-device fit:** runs offline on low-RAM, CPU-only hardware.

Beyond working code, the submission should show engineering judgement: each design choice
backed by a measurement, and honest reporting of limits.

## 2. Goals, non-goals, anti-goals

**Goals**
- One command produces the unique plate list (console, JSON, CSV) with peak memory and timings.
- Peak RAM comfortably under 512 MB (target ≤ 400 MB in the default mode).
- High recall on plates a human can read in the clips; no duplicate variants.
- A measured accuracy figure against a small hand-checked ground truth for the two clips.
- Presets that trade speed for memory, to show the design scales down to small devices.

**Non-goals**
- Real-time or live-stream processing.
- Training models (pretrained only).
- Merging results across separate clips (each run handles one video).
- Vehicle detection, tracking of vehicles, or any feature beyond plate reading.
- Plate formats outside the US (keep the format layer pluggable, but implement US only).

**Anti-goals (a failure even if it runs)**
- Peak RAM over 512 MB in any preset.
- The same plate listed twice with slightly different text.
- Any network access at runtime, or PyTorch / TensorFlow / Ultralytics imported at runtime.
- Logic tuned to the specific clips (file names, timestamps, hard-coded regions or plates).

## 3. Constraints and input facts

From REQUIREMENTS.md: offline, CPU only, pretrained models allowed, ≤ 512 MB for the whole
process, video path on the command line, no assumptions about length or resolution.

Measured on the supplied clips (details in CLAUDE.md, "Input video facts"):

- 3840×2160, 30 FPS, 60 s, **HEVC (H.265)**, about 40 Mbps, keyframe every 30 frames, no B-frames.
- **California, USA plates** (e.g. `9KTA262`, `7ZZV186`, `9VCA051`), not Indian as the
  requirements assumed. Standard format: digit, 3 letters, 3 digits.
- Plates are 55–180 px wide at full resolution (60–80 px typical for parked cars).
- Burned-in overlay strip (date, GPS, speed) along the bottom ~4% of the frame.
- Blue colour cast, some sun glare. Clip 2 is stationary behind the same car for about 40 s.
- Decoder memory depends on thread count: 1 thread 138 MB, 4 threads 229 MB, auto 624 MB.

## 4. Architecture

```
            ┌───────────── per sampled frame ─────────────┐
 Video ─► Decoder ─► Change gate ─► Tiled detector ─► Crop + enhance ─► Plate OCR ─► Reading
 (PyAV,     (skip if scene   (YOLOv9-t, 6×640      (full-res 4K,       (fast-plate-ocr
  2–4 thr)   unchanged)       tiles on 1080p)       WB + CLAHE)          CCT-S, per-char probs)
                                                                                 │
                     Tracker (IoU + text) ─► per-track probability voting ◄──────┘
                                   │
                     Post-merge near-duplicates ─► Format scoring ─► Filter ─► Console / JSON / CSV
```

### 4.1 Decoding (`anpr/video.py`)

- **PyAV**, not `cv2.VideoCapture`. Reason: explicit decoder thread control
  (`thread_type="FRAME"`, `thread_count=N`), access to raw planes, and `skip_frame` support.
  OpenCV's commonly cited `OPENCV_FFMPEG_CAPTURE_OPTIONS="threads;N"` does not set decoder
  threads.
- Every frame is decoded (P-frames depend on previous frames). Only frames on the sampling grid
  (`--sample-fps`, default 5) are converted to arrays; the rest are dropped immediately.
- A sampled frame is converted once to a full-resolution BGR array (about 25 MB) and freed after
  its crops are taken. At most one full-res frame is alive at a time.
- Timestamps come from `frame.pts × time_base`, not from frame counting.

### 4.2 Change gate (`anpr/gate.py`)

- A 160×90 grayscale thumbnail of each sampled frame is compared with the last *processed* frame
  (mean absolute difference).
- If the scene is unchanged (below `--static-threshold`), detection and OCR are skipped. Tracks that
  were visible in the last processed frame have their `last_seen` extended to this timestamp and
  do not age.
- This is the classical "pre-processing" step in the design. It costs well under 1 ms and saves
  most of the work in stationary stretches such as clip 2.

### 4.3 Detection (`anpr/detector.py`)

- Model: **open-image-models `yolo-v9-t-640-license-plates-end2end.onnx`** (about 7.8 MB, NMS
  built into the graph, MIT code licence). Loaded directly with ONNX Runtime. The pip package is
  not a runtime dependency.
- The full frame is resized to 1920×1080 (plates become 28–90 px wide) and split into a 3×2 grid
  of 640×640 tiles with overlap (x = 0, 640, 1280; y = 0, 440). Boxes are mapped back to 4K
  coordinates, then cross-tile duplicates are removed with NMS.
- Ignore detections whose centre is in the bottom `--ignore-bottom` fraction of the frame
  (default 0.05, the overlay strip). Plates on the car directly ahead sit at about 80% of frame
  height, so the mask must stay small.
- Drop boxes narrower than `--min-plate-width` (default 40 px at full res): too small to read.
- `--det-mode full` (single 640 pass) exists for the low-memory preset and for comparison.

### 4.4 Crop and enhance (`anpr/crop.py`)

- Crop from the **full-resolution** frame with 8% padding, clamped to the frame, and `.copy()`.
- Gray-world white balance (fixes the blue cast), then CLAHE on the L channel in LAB
  (clip limit 2.0, 4×4 tiles).
- Resize to the OCR input size with `INTER_CUBIC` when upscaling, `INTER_AREA` when downscaling.
- A sharpness score (variance of the Laplacian) is stored with each crop for weighting votes.

### 4.5 OCR (`anpr/ocr.py`)

- Model: **fast-plate-ocr `cct-s-v2-global-model`** (5.3 MB ONNX, MIT, trained data includes the
  United States; 94.6% exact-plate accuracy on its US validation split).
- Fixed slot output: per-slot probabilities over `0-9A-Z_`, where `_` is padding. The wrapper
  returns the raw slot probability matrix, not just a string, so the tracker can vote on
  probabilities.
- Loaded from local files (ONNX plus its YAML config) with a thin wrapper. The `fast-plate-ocr`
  package is used only by the setup script to fetch the files, never at runtime, so no hub
  download can happen during a run.
- Crops are batched per frame where the model allows a dynamic batch dimension; otherwise one
  inference per crop.

### 4.6 Tracking and voting (`anpr/tracker.py`)

- Each processed frame's readings are matched to active tracks by IoU of full-res boxes
  (threshold 0.2, low because the camera moves between samples). If IoU fails, a reading may
  still join a track whose current best text is within edit distance 1 and whose last box is
  nearby (centre distance under 1.5× box width).
- A track ends after `--track-max-age` processed frames (default 3) without a match.
- Each track keeps its best K readings (default 15), ranked by detector score × sharpness.
- **Final text by probability voting:** for readings with the dominant plate length, sum the
  per-slot probability vectors weighted by reading quality; take the argmax per slot. This uses
  the full OCR output instead of majority voting on strings.
- **Post-merge:** after all frames, merge tracks with identical final text, and tracks within
  edit distance 1 when one of them has under half the readings of the other. Merged entries keep
  the earliest `first_seen`, latest `last_seen`, and summed `readings`.

### 4.7 Format scoring and confidence (`anpr/plate_format.py`)

- Normalise: uppercase, strip non-alphanumerics and padding.
- Formats are data, defined in one table. US set:
  - California standard `^[0-9][A-Z]{3}[0-9]{3}$` (prior weight 1.0)
  - Older California `^[A-Z]{3}[0-9]{3}$` and `^[0-9][A-Z]{3}[0-9]{2,3}$` variants, other common US
    patterns, and a generic 2–8 alphanumeric fallback (prior weight 0.5).
- Positional confusion fixes (`O↔0`, `I↔1`, `B↔8`, `S↔5`, `Z↔2`) are applied only when the
  text has exactly the length of a template and the fix makes it match. When the slot
  probabilities are available, the fix chooses the highest-probability character of the
  required class (letter or digit) in that slot, rather than a fixed mapping table.
- Readings that match no template are kept with the fallback weight; never dropped.
- Final confidence = mean over slots of the winning-character vote share × mean reading
  confidence × format weight, clipped to [0, 1].
- Output filter: keep a plate if confidence ≥ `--min-confidence` (default 0.5) **and**
  (readings ≥ `--min-readings`, default 2, **or** confidence ≥ 0.85).

### 4.8 Output (`anpr/output.py`)

- Console table sorted by `first_seen`, then the run summary (time per stage, peak memory).
- `<out-dir>/<video-stem>.plates.json` with `plates` (fields from REQUIREMENTS.md §3) and `run`
  (video facts, preset, settings, model files and hashes, stage timings, peak memory, frames
  decoded / sampled / processed / skipped by the gate).
- `<out-dir>/<video-stem>.plates.csv` with the plate fields only.
- `--debug` saves every crop with its reading and the track it joined to `<out-dir>/debug/`,
  plus a contact sheet per final plate. This is also how the ground truth is checked (§6).

### 4.9 Memory and runtime settings (`anpr/runtime.py`)

Applied at start-up, before importing numpy, cv2 or onnxruntime:

- `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS` set to the ORT thread count. Without this, numpy
  on Windows commits about 500 MB of private memory at import.
- ONNX Runtime sessions: `enable_cpu_mem_arena=False`, `enable_mem_pattern=False`,
  `inter_op_num_threads=1`, `intra_op_num_threads` from the preset, `ORT_ENABLE_ALL`
  optimisation, CPU provider only.
- `cv2.setNumThreads` set to the same small number.

Peak memory is reported from inside the process (`psutil` `peak_wset` on Windows, `ru_maxrss` on
Linux/macOS) and a background sampler records the RSS high-water mark per stage.

**Which number counts (design author's call):** the 512 MB limit applies to peak resident
memory (working set / RSS), which is what "RAM used" means in Task Manager's default view and in
`/usr/bin/time -v`. The run also reports peak private/commit bytes (`peak_pagefile` on Windows,
peak `VmData`-style private memory on Linux where available) and the README shows both, so a
reader using a different tool sees no surprise. The `OPENBLAS_NUM_THREADS=1` setting keeps commit
close to RSS; a preset whose peak commit exceeds 512 MB is also treated as a bug.

### 4.10 Presets

| Preset | Decoder threads | ORT threads | Sample FPS | Detector | Intent |
|---|---|---|---|---|---|
| `low-mem` | 1 | 1 | 3 | `full` mode, INT8 if available | Smallest footprint, small devices |
| `balanced` (default) | 4 | 4 | 5 | tiled | Best overall trade-off |
| `fast` | 6 | 6 | 5 | tiled, INT8 if available | Speed on a capable laptop, still ≤ 512 MB |

All thread counts are capped at the machine's physical core count. Decoder threads above 4 have
not been measured yet: the first benchmark must measure 6, and if peak memory goes over 450 MB the
`fast` preset uses 4 decoder threads instead. Every individual setting can be overridden on the
command line.

### 4.11 Supported platforms (design author's call)

The interviewer's machine is unknown, so the tool targets Windows 10/11 and Linux on x86-64,
Python 3.11–3.13, with pinned versions that have wheels for all of these. Nothing is
x86-specific, so ARM64 Linux (e.g. Raspberry Pi 5) should work but is not verified. On first run
the tool checks that PyAV can decode the input codec and exits with a clear message if not. The
README states exactly where the published numbers were measured. The README reports measured peak
memory, time and accuracy for all three presets on both clips.

## 5. Command line

```
python main.py <video_path> [--preset balanced] [--out-dir output/] [--sample-fps 5]
               [--decode-threads N] [--threads N] [--det-mode tiled|full]
               [--det-conf 0.3] [--min-confidence 0.5] [--min-readings 2]
               [--min-plate-width 40] [--ignore-bottom 0.05] [--static-threshold 2.0]
               [--track-max-age 3] [--debug]
```

All defaults live in one `Config` dataclass; presets are dictionaries of overrides.

## 6. Evaluation

Accuracy is a judging criterion, so the project measures it rather than claiming it.

- `eval/ground_truth/<video-stem>.json` lists the plates a human can read in each clip, with
  approximate first-seen time. It is drafted from a `--debug` run (crops and contact sheets),
  then checked and corrected by a person viewing the clips. The builder must not treat an
  unverified draft as ground truth.
- `scripts/evaluate.py` compares a result JSON with the ground truth: exact-match recall,
  precision, character error rate on matched plates, and duplicate count.
- `scripts/benchmark.py` runs each preset on each clip and writes the table used in the README
  (peak memory, wall time, per-stage time, recall, precision).

**Avoiding tuning to the test set (design author's call):** thresholds and INT8 calibration are
tuned on clip 1 (`REC…7377`) only. Clip 2 (`REC…7378`) is the held-out clip. The README reports
accuracy on both, labelled "tuning clip" and "held-out clip", so the interviewer can see the
held-out number is not tuned. Defaults are fixed before the held-out clip is evaluated.

**Interim check before ground truth exists:** the first milestone uses a quick manual look at
`--debug` contact sheets from clip 1 (does the detector box the plates a human can see, does OCR
read them) to decide whether the chosen models are usable. Formal numbers wait for the verified
ground truth.

## 7. Optional: INT8 detector

`scripts/quantize_detector.py` (dev-only, needs the `onnx` package) produces a statically
quantised QDQ detector, calibrated on about 200 tiles sampled from clip 1 only. Research measured
about 2× speed and 4× smaller files for static INT8 on a similar conv model; dynamic
quantisation was slower and must not be used for the detector. The INT8 model is used only if its
recall on the ground truth is within 2 points of FP32; otherwise presets fall back to FP32. This
is a stretch goal, done after everything else works.

## 8. Repository layout

```
main.py
anpr/
  config.py  runtime.py  video.py  gate.py  detector.py  crop.py  ocr.py
  plate_format.py  tracker.py  output.py  metrics.py  pipeline.py
scripts/
  setup_models.py        # downloads models once, verifies SHA-256, writes models/MODELS.md
  evaluate.py  benchmark.py  quantize_detector.py
eval/ground_truth/
models/                  # git-ignored model files + MODELS.md
tests/
output/                  # committed sample results
requirements.txt         # runtime, pinned: av, numpy, opencv-python-headless, onnxruntime, psutil
requirements-dev.txt     # pytest, onnx, fast-plate-ocr (setup only)
README.md
```

## 9. Alternatives considered

| Option | Decision | Why |
|---|---|---|
| Classical localisation (edges, MSER, Haar) as main detector | Rejected | No US-ready cascade outside AGPL OpenALPR; on moving dashcam footage it has high false-positive rates and fails on glare and angles. The cheap classical part kept is the change gate and region mask. |
| Classical pre-filter proposing regions for the detector | Deferred | Adds tuning risk for a 6-tile workload that is already affordable. Revisit only if detection dominates runtime. |
| Two-stage: vehicle detector then plate detector | Rejected | Extra model and memory; misses plates when a vehicle is missed. With tiling, plates are already 28–90 px at the detector. |
| Single 640 pass on the full frame | Kept only for `low-mem` | Plates shrink to 10–30 px and are often missed. |
| OpenCV `VideoCapture` decoding | Rejected | No reliable decoder thread cap before 4.7 and no raw plane access; PyAV gives both. |
| Ultralytics YOLOv8/11 plate weights | Rejected | AGPL licence and an export step needing torch. |
| NVIDIA TAO LPDNet / LPRNet (US) | Not in default | Trained on US/California dashcam data, but NVIDIA EULA, NGC download and a 55 MB OCR model. May be added later as a comparison only. |
| PaddleOCR / RapidOCR, Tesseract, EasyOCR | Rejected | General text readers pick up the "California" script and stickers (Paddle/Tesseract), or need torch (EasyOCR). |

## 10. Left to the builder

- Exact tile overlap and NMS thresholds, within the stated grid.
- Whether OCR runs batched or per crop (measure both).
- Internal data structures, logging format, and test fixtures.
- How the RSS sampler is implemented, as long as it reports the true peak.

## 11. Risks

| Risk | Mitigation |
|---|---|
| open-image-models detector trained mostly on non-US plates; weight licence not stated | Measure recall on the ground truth early (first milestone). Note the licence question in the README. Fallback: LPDNet US as a comparison model. |
| fast-plate-ocr confuses the "California" script or stickers | Check on real crops in the first milestone; if needed, trim the top ~20% of the crop before OCR. |
| PyAV wheel lacks HEVC or thread control on some platform | Verify on day one. Fallback: `cv2.VideoCapture` with `CAP_PROP_N_THREADS` (OpenCV ≥ 4.7). |
| Fast ego-motion breaks IoU tracking at 5 FPS | Text-similarity matching and post-merge catch split tracks. |
| Memory estimate wrong | Peak RSS is measured on every run; any preset over 450 MB is treated as a bug. |

## 12. Evidence

- Decoder memory and time: measured with the ffmpeg CLI on clip 1, i9-11950H (CLAUDE.md).
- ONNX Runtime and numpy memory, quantisation timings: measured by a research agent on this
  machine with a 6.3 MB stand-in conv model (Python 3.13, ORT 1.23.2): numpy+ORT+model about
  57 MB working set; arena off saves about 13 MB; static INT8 about 2× faster.
- Models: open-image-models README and release assets; fast-plate-ocr model zoo and release
  `*_val_results.json`; NVIDIA NGC model cards for LPDNet and LPRNet; morsetechlab model card
  (warns that small plates need 1280 or tiling).
- OpenCV FFmpeg thread handling: `cap_ffmpeg_impl.hpp` (thread option is `CAP_PROP_N_THREADS`
  or `OPENCV_FFMPEG_THREADS`, OpenCV ≥ 4.7).

## 13. Changes made during implementation (2026-11-14)

Each change came from a measurement or from looking at real crops. Where this section and
the earlier sections disagree, this section wins.

| Design said | Built | Why |
|---|---|---|
| 1920 px wide frame, 6 to 8 tiles per frame | 1280 px wide, 3 tiles, top 15% skipped, only every 3rd processed frame; single 640 pass otherwise | 8 tiles cost about 750 ms a frame. Single pass already finds plates of about 80 px and wider |
| ONNX arena off | Arena off, `--arena` option | Off: 371 MB, on: 517 MB on clip 2 for about 15% speed |
| Decode in the main loop | Decode in a producer thread (`prefetch`, depth 1) | Decoding is the bottleneck and now overlaps detection |
| Fixes to template when 2 or fewer slots change | Fix only if it costs at most log(3) nats; look-alike pairs cost at most 0.3 | Forcing a confident motorcycle plate into the car format gave a wrong answer |
| One format (plus two older) | Added California motorcycle `1A23456` | `8D94661` in clip 1 |
| Fallback weight 0.5 | 0.75 | 0.5 would drop every vanity plate under the confidence filter |
| Badge lettering filtered by a text rule (considered) | Filtered by box shape, aspect 0.9 to 2.8 | `EABLEB` is a real all-letter plate, so a text rule would lose it |
| Merge near duplicates when the smaller has under half the readings | Also merge when within 2 s of each other | One plate was split into 4 fragments with 1 to 2 readings each |
| Confidence at least 0.5; 2 readings or 0.85 | At least 0.35; 2 readings or 0.65 | Chosen from clip 1's unfiltered tracks |
| `fast` used 6 decoder threads | 4 threads, 4 fps, deep pass every 4th frame, no enhanced OCR variant | More threads raise memory (about 50 MB each) |
| INT8 detector (stretch) | Not built | Detection is no longer the bottleneck |
| Ground truth checked by a person | Labelled by Claude from enlarged crops; marked DRAFT | No human check yet. Clip 2's raw output was seen before thresholds were set |
