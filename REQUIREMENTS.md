# Requirements: Offline Number Plate Recognition from Dashcam Video

## 1. Goal

Build a program that reads a dashcam video and outputs the list of all unique
vehicle number plates seen in it. The program runs fully on the local machine
and stays within a 512 MB memory budget.

## 2. Input

| Property | Expected value |
|---|---|
| Format | Video file (MP4 or similar container) |
| Duration | About 1 minute per clip |
| Resolution | 4K (3840 × 2160) |
| Frame rate | 30 FPS (about 1,800 frames per clip) |
| File size | About 300–350 MB |
| Clips | One or two, supplied by the interviewer |

The program must accept the video path as a command-line argument and must not
assume a fixed file name, length or resolution.

## 3. Output

A list of unique number plates found in the video, written to:

- the console, as a readable list, and
- a JSON file and a CSV file.

Each plate entry contains:

| Field | Required | Description |
|---|---|---|
| `plate` | Yes | Normalised plate text, uppercase, no spaces (for example `MH12AB1234`) |
| `first_seen` | Yes | Timestamp in the video when the plate was first read (`mm:ss.sss`) |
| `last_seen` | Should | Timestamp of the last reading |
| `confidence` | Yes | Combined confidence for the final reading, 0 to 1 |
| `readings` | Should | Number of frames that contributed to this plate |

Each physical plate must appear **once** in the output, even though it is
visible in many frames.

## 4. Functional requirements

| ID | Requirement |
|---|---|
| F1 | Decode the video frame by frame. Never load the whole file into memory. |
| F2 | Sample frames instead of processing all 30 per second. The sampling rate must be configurable. |
| F3 | Detect number plate regions in each sampled frame using a lightweight detector. |
| F4 | Crop each detected plate from the **full-resolution** frame so OCR gets maximum detail. |
| F5 | Read the plate characters with an OCR model. |
| F6 | Normalise OCR text: uppercase, strip spaces and punctuation, fix common confusions (`O`/`0`, `I`/`1`, `B`/`8`, `S`/`5`) based on character position in the plate format. |
| F7 | Validate readings against the expected registration format for the plates in the video. Readings that fail validation are kept with lower confidence, not silently dropped, so unusual legitimate plates are not lost. |
| F8 | Merge repeated readings of the same plate across frames (tracking and/or voting on text similarity) into one entry with the most likely text. |
| F9 | Discard readings below a configurable confidence threshold. |
| F10 | Write the results in the format described in section 3. |

## 5. Constraints

| ID | Constraint |
|---|---|
| C1 | **Memory:** peak RAM of the whole process must stay at or below **512 MB**. This includes model weights, the inference runtime, video decoding, frame buffers and the Python runtime. |
| C2 | **Offline:** no cloud APIs or network calls at runtime. Models are downloaded once during setup and loaded from local files. |
| C3 | **Pretrained models:** pretrained models (for example from Hugging Face) may be used. Training from scratch is not required. |
| C4 | **Hardware:** must run on CPU only. A GPU may be used if present but must not be required. |
| C5 | **Scope:** only plate recognition. No dashcam recording, UI, alerts or other features. |

## 6. Proposed technical approach

| Stage | Choice | Reason |
|---|---|---|
| Video decoding | OpenCV (`cv2.VideoCapture`), with frame skipping | Streams frames one at a time; low memory |
| Plate detection | YOLOv8n / YOLO11n licence-plate weights, exported to ONNX | Small (about 6 MB), fast on CPU |
| Inference runtime | ONNX Runtime (CPU) | Much lighter than full PyTorch at runtime |
| OCR | Lightweight plate OCR model (CRNN / LPRNet style, ONNX) | Small footprint compared with EasyOCR or full PaddleOCR |
| Merging | Simple IoU tracker plus per-character voting | Combines many noisy readings into one reliable result |
| Output | JSON and CSV | Easy to read and check |

PyTorch must not be loaded at runtime, since it alone can use several hundred MB.
It may be used only in a one-time model export step, if needed.

## 7. Acceptance criteria

The assignment is complete when:

1. `python main.py <video_path>` runs end to end on the supplied video with no internet connection.
2. It prints and saves the list of unique plates in the format in section 3.
3. Each plate clearly visible in the video appears once, with no duplicate variants of the same plate.
4. Peak memory is measured and reported, and is at or below 512 MB.
5. Total processing time for the clip is measured and reported.
6. A `README.md` explains setup, how to run it, the models used, and the measured memory and time.

## 8. Deliverables

- Source code with a single command-line entry point.
- A setup script or instructions that download the models once.
- `requirements.txt` with pinned versions.
- `README.md` with results: plates found, peak memory, processing time, known limitations.
- Sample output files (JSON and CSV) from the supplied video.

## 9. Open points

| Question | Current assumption |
|---|---|
| Which country's plates appear in the video? | To be confirmed by inspecting the video. Indian format (`MH 12 AB 1234`) is assumed until then. |
| Target hardware (ARM or x86)? | x86 CPU for development; nothing x86-specific is used, so it should port to ARM. |
| Are partially visible or far-away plates expected? | Only plates readable by a human viewer are expected in the output. |
