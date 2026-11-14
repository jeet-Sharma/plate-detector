# ruff: noqa: E501  (layout data: one box or arrow per line reads better than wrapped coordinates)
"""Draw the architecture diagram: docs/architecture.svg (and the PNG via ui_screenshots-style render).

    python scripts/draw_architecture.py

Every number in the drawing comes from the code's settings or from output/benchmark/results.json.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "architecture.svg"

W, H = 2400, 1720
PAPER, GRID = "#FBFCFD", "#EEF2F6"
INK, DIM, LINE = "#15202B", "#5A6878", "#B9C5D2"
TEAL, TEAL_FILL = "#0E8F82", "#E6F5F2"
ORANGE, ORANGE_FILL = "#C2570C", "#FFF3EA"
RED = "#C0392B"
STORE_FILL = "#F1F4F8"
SANS = "Segoe UI, Inter, Helvetica, Arial, sans-serif"
MONO = "Cascadia Mono, Consolas, Menlo, monospace"

parts: list[str] = []
warnings: list[str] = []


def t(x, y, s, size=12.5, weight=400, fill=INK, anchor="start", mono=False, italic=False):
    family = MONO if mono else SANS
    style = ' font-style="italic"' if italic else ""
    parts.append(
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{style}>{escape(s)}</text>'
    )


def width_of(s, size=12.5, mono=False):
    return len(s) * size * (0.61 if mono else 0.53)


def rect(x, y, w, h, fill="#FFFFFF", stroke=INK, sw=1.4, rx=10, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"{d}/>'
    )


def box(x, y, w, h, title, lines=(), kind="part", mono_lines=False):
    fills = {
        "part": ("#FFFFFF", INK),
        "model": (TEAL_FILL, TEAL),
        "store": (STORE_FILL, DIM),
        "net": (ORANGE_FILL, ORANGE),
        "warn": ("#FDECEA", RED),
    }
    fill, stroke = fills[kind]
    rect(x, y, w, h, fill, stroke, 1.6 if kind == "model" else 1.3)
    t(x + 12, y + 23, title, 14.5, 650, TEAL if kind == "model" else INK)
    for i, line in enumerate(lines):
        t(x + 12, y + 44 + i * 17, line, 12.5, 400, INK if i < 99 else DIM, mono=mono_lines)
        if width_of(line, 12.5, mono_lines) > w - 20:
            warnings.append(f"long line in '{title}': {line!r}")
    if width_of(title, 14.5) > w - 20:
        warnings.append(f"long title: {title!r}")
    if 44 + (len(lines) - 1) * 17 + 10 > h:
        warnings.append(f"box too short: {title!r}")
    return {"l": (x, y + h / 2), "r": (x + w, y + h / 2), "t": (x + w / 2, y), "b": (x + w / 2, y + h)}


def arrow(points, color=INK, label=None, at=None, dash=None, sw=1.6, marker="arrow", size=11.5):
    d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    dd = f' stroke-dasharray="{dash}"' if dash else ""
    mid = {INK: "arrow", TEAL: "arrowTeal", ORANGE: "arrowOrange", DIM: "arrowDim", RED: "arrowRed"}
    parts.append(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dd} '
        f'marker-end="url(#{mid.get(color, marker)})"/>'
    )
    if label:
        lx, ly = at if at else (
            (points[0][0] + points[-1][0]) / 2,
            (points[0][1] + points[-1][1]) / 2 - 7,
        )
        lw = width_of(label, size) + 10
        parts.append(
            f'<rect x="{lx - lw / 2}" y="{ly - 12}" width="{lw}" height="16" rx="4" fill="{PAPER}" '
            'opacity="0.92"/>'
        )
        t(lx, ly, label, size, 500, color if color != INK else DIM, "middle")


def zone(x, y, w, h, label, color=LINE, dash="7 6", fill="none"):
    rect(x, y, w, h, fill, color, 1.4, 16, dash)
    lw = width_of(label, 14) + 18
    rect(x + 18, y - 12, lw, 24, PAPER, PAPER, 0, 6)
    t(x + 27, y + 5, label, 14, 700, color if color != LINE else DIM)


# ---------------------------------------------------------------- canvas
parts.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
    'role="img" aria-label="Plate Stream architecture: one-time model setup, two entry points, the '
    'per-frame pipeline across the decoder and main threads, end-of-video voting, the browser to '
    'server protocol of the live UI, and measured memory and latency.">'
)
parts.append(
    "<defs>"
    f'<pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" '
    f'fill="none" stroke="{GRID}" stroke-width="1"/></pattern>'
    + "".join(
        f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{col}"/></marker>'
        for name, col in (
            ("arrow", INK), ("arrowTeal", TEAL), ("arrowOrange", ORANGE),
            ("arrowDim", DIM), ("arrowRed", RED),
        )
    )
    + "</defs>"
)
rect(0, 0, W, H, PAPER, PAPER, 0, 0)
parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="url(#grid)"/>')

# ---------------------------------------------------------------- header + legend
t(40, 66, "Plate Stream", 32, 750)
t(40, 96, "Offline number-plate recognition from 4K dashcam video: architecture, protocols and "
  "measured numbers", 16, 400, DIM)
legend = [
    (INK, None, "pipeline data"),
    (DIM, "6 5", "control (skip, touch)"),
    (TEAL, "8 5", "live UI events / HTTP"),
    (ORANGE, "8 5", "internet (setup only)"),
]
lx = 1430
for color, dash, label in legend:
    arrow([(lx, 76), (lx + 46, 76)], color, dash=dash)
    t(lx + 56, 80, label, 13, 500, DIM)
    lx += 76 + width_of(label, 13) + 34
rect(1430, 96, 22, 16, TEAL_FILL, TEAL, 1.6, 4)
t(1460, 109, "ONNX model", 13, 500, DIM)
rect(1580, 96, 22, 16, STORE_FILL, DIM, 1.3, 4)
t(1610, 109, "file / store", 13, 500, DIM)
rect(1720, 96, 22, 16, "#FFFFFF", INK, 1.3, 4)
t(1750, 109, "code component", 13, 500, DIM)
t(1910, 109, "times = mean per call, clip 7378, balanced", 13, 500, DIM)

# ---------------------------------------------------------------- 1. setup
zone(40, 160, 620, 280, "1  One-time setup (the only step with internet)", ORANGE, "8 6")
a1 = box(62, 200, 175, 150, "GitHub releases", ["open-image-models", "cnn-ocr-lp", "fixed HTTPS URLs"], "net")
a2 = box(262, 200, 185, 150, "setup_models.py", ["urllib download", "SHA-256 check", "skips files already", "verified"])
a3 = box(472, 200, 170, 220, "models/  13 MB", [
    "detector .onnx", "  7.8 MB", "OCR .onnx", "  5.3 MB", "OCR config .yaml", "read from disk only",
], "store")
arrow([a1["r"], a2["l"]], ORANGE, "HTTPS", dash="8 5")
arrow([a2["r"], a3["l"]], INK, "verify")

# ---------------------------------------------------------------- process boundary + entry points
zone(690, 160, 1670, 950, "One Python process: CPU only, no network, peak RAM at most 512 MB", TEAL, "2 0")
b1 = box(720, 200, 255, 105, "CLI: python main.py <video>", [
    "--preset low-mem | balanced | fast", "exit 2 bad video, 3 no model",
])
b2 = box(720, 320, 255, 105, "UI: python -m ui", [
    "stdlib HTTP, 127.0.0.1:8765", "one clip at a time",
])
b3 = box(1010, 200, 270, 225, "Before numpy loads", [
    "OPENBLAS / OMP / MKL threads = 1", "(otherwise numpy commits", " about 500 MB on Windows)",
    "cv2.setNumThreads(4)", "anpr/__init__ stays light",
])
b4 = box(1315, 200, 315, 225, "Config (frozen dataclass)", [
    "balanced: 4 decoder threads,", "  5 fps, deep pass 1 in 3",
    "fast: 4 threads, 4 fps,", "  deep 1 in 4, one OCR variant",
    "low-mem: 1 thread, 3 fps, quick only", "threads capped at physical cores",
])
b5 = box(1665, 200, 320, 225, "pipeline.run(...)", [
    "run(config,", "  source_factory=None,", "  observer=None,", "  cancel=None)",
    "-> RunSummary", "   + JSON + CSV",
], mono_lines=True)
b6 = box(2020, 200, 315, 225, "ONNX Runtime 1.32, CPU", [
    "intra-op threads 4, inter-op 1", "graph optimisation: all",
    "memory arena off by default", "  (on: +146 MB on clip 2)",
    "each model loaded once", "two sessions: detector, OCR",
], "model")
arrow([b1["r"], (995, 252), (995, 280), (1010, 280)], INK)
arrow([b2["r"], (995, 372), (995, 340), (1010, 340)], INK, "same start-up", at=(940, 448))
arrow([b3["r"], b4["l"]], INK)
arrow([b4["r"], b5["l"]], INK, "build_config")
arrow([b5["r"], b6["l"]], INK, "load")
arrow([(557, 200), (557, 136), (2177, 136), (2177, 200)], DIM, "model files, read once at start (no download)",
      at=(1360, 132), dash="6 5")

# ---------------------------------------------------------------- 2. per-frame pipeline
zone(712, 470, 1628, 615, "2  Per-frame pipeline", LINE)
# lanes
rect(722, 500, 1608, 180, "#F6F9FB", "#F6F9FB", 0, 10)
rect(722, 695, 1608, 210, "#F6F9FB", "#F6F9FB", 0, 10)
t(732, 520, "DECODER THREAD (prefetch)", 12, 700, DIM)
t(732, 715, "MAIN / JOB THREAD, every sampled frame", 12, 700, DIM)
t(732, 925, "END OF VIDEO (and live snapshots for the UI)", 12, 700, DIM)

# setup row left side reused: source + decode on the left of the process
d1 = box(62, 520, 240, 150, "Video source", [
    "file path (CLI, sample clips)", "or GrowingFile reader:", "an upload still arriving",
    "moov first: read while uploading",
], "store")
t(62, 505, "INPUT", 12, 700, DIM)
d2 = box(742, 530, 300, 140, "PyAV / FFmpeg HEVC decode", [
    "every frame: 1,801 per clip", "4 frame threads", "27.8 ms per frame",
    "auto threads alone: 624 MB",
])
d3 = box(1067, 530, 270, 140, "Sampling grid", [
    "5 fps = 1 frame in 6", "160 x 90 grey thumbnail", "time = pts x time_base",
    "unsampled frames: no pixels",
])
d4 = box(1362, 530, 255, 140, "prefetch() queue", [
    "depth 1, about 12 MB YUV", "decoding overlaps models", "errors re-raised in main",
])
d5 = box(1642, 530, 330, 140, "LiveReporter (UI only)", [
    "frame: 960 px JPEG q70 + boxes", "reading: 128 px crop + char p",
    "plates every 0.35 s, stats every 1 s", "CLI uses NullReporter: no cost",
])
arrow([d1["r"], (722, 595), d2["l"]], INK, "bytes", at=(520, 588))
arrow([d2["r"], d3["l"]], INK, "frames")
arrow([d3["r"], d4["l"]], INK, "sampled")

m1 = box(742, 735, 215, 150, "Change gate", [
    "mean |thumb diff| < 2.0", "-> skip, 0.3 ms", "forced after 10 skips",
    "clip 2: 177 of 301 skipped",
])
m2 = box(977, 735, 175, 150, "Convert", ["YUV -> 4K BGR", "25 MB, 3.1 ms", "one frame alive", "freed after crops"])
m3 = box(1172, 735, 320, 150, "Plate detector  YOLOv9-t", [
    "quick: 1 x 640 letterbox, 109 ms", "deep (1 in 3): 1280 px wide,", "  3 overlapping tiles, 300 ms",
    "NMS inside the ONNX graph", "+ tile de-duplication",
], "model")
m4 = box(1512, 735, 200, 150, "Box filters", [
    "score >= 0.30", "width >= 40 px (4K)", "aspect 0.9 to 2.8", "skip top 15%, bottom 5%",
])
m5 = box(1732, 735, 260, 150, "Crop from full 4K", [
    "+8% padding, clamped", "A: as is", "B: white balance + CLAHE", "-> 64 x 128 RGB uint8",
])
m6 = box(2012, 735, 308, 150, "Plate OCR  CCT-S", [
    "in  (N, 64, 128, 3) uint8", "out (N, 10, 37) probabilities", "A and B averaged",
    "88 ms per frame (all plates)",
], "model")
arrow([d4["b"], (1490, 700), (849, 700), m1["t"]], INK, "next sampled frame", at=(1170, 696))
arrow([m1["r"], m2["l"]], INK, "changed", at=(967, 802))
arrow([m2["r"], m3["l"]], INK, "BGR", at=(1162, 802))
arrow([m3["r"], m4["l"]], INK)
arrow([m4["r"], m5["l"]], INK)
arrow([m5["r"], m6["l"]], INK)

# end-of-frame + end-of-video row (right to left)
r1 = box(2012, 945, 308, 125, "Reading + Tracker", [
    "conf = det score x mean char p", "match IoU >= 0.2, else text 1 edit",
    "end after 3 missed frames", "keep best 15 readings per track",
])
v1 = box(1690, 945, 300, 125, "Per-track vote", [
    "sum slot probabilities x weight", "weight = conf x sharpness",
    "dominant text length wins", "share = winner's mean probability",
])
v2 = box(1352, 945, 318, 125, "Format resolve (California)", [
    "1ABC234 (1.0)  1A23456 (0.95)", "ABC123, 123ABC (0.9), other (0.75)",
    "fix a slot only if it costs <= log 3", "0/O 1/I 8/B 5/S 2/Z 6/G cheap",
])
v3 = box(1052, 945, 280, 125, "Merge duplicates", [
    "same text -> one plate", "1 edit apart and", "  < half the readings or <= 2 s",
    "votes pooled, track ids kept",
])
v4 = box(742, 945, 290, 125, "Accept + write", [
    "confidence >= 0.35 and", "  >= 2 readings or >= 0.65", "console, <stem>.plates.json/csv",
    "run report: timings, memory",
])
arrow([m6["b"], r1["t"]], INK, "readings", at=(2205, 920))
arrow([r1["l"], v1["r"]], INK)
arrow([v1["l"], v2["r"]], INK)
arrow([v2["l"], v3["r"]], INK)
arrow([v3["l"], v4["r"]], INK)
arrow([m1["b"], (849, 912), (2060, 912), (2060, 945)], DIM, "unchanged: touch() extends last_seen",
      at=(1500, 908), dash="6 5")
arrow([(2290, 945), (2290, 905), (2345, 905), (2345, 600), d5["r"]], TEAL,
      "events", at=(2318, 760), dash="8 5")

# measurement strip on the left, outside the pipeline zone
s0 = box(62, 735, 600, 335, "Measurement (every run, both modes)", [
    "StageTimers: every call timed -> mean, p50, p95, max per component",
    "decode time recorded for each of the 1,801 source frames",
    "MemoryMonitor: resident memory sampled every 50 ms in a thread,",
    "  plus the OS peak counter (Windows peak_wset, Linux VmHWM)",
    "peak private (commit) memory reported beside resident memory",
    "throughput: processing time / video length = real-time factor",
    "frames: sampled, analysed, skipped by the gate, detector passes",
    "benchmark.py: one fresh process per run, memory also measured",
    "  from outside the process tree (agrees within 5 MB)",
    "evaluate.py: plates found, precision, character accuracy",
    "  against eval/ground_truth (draft, labelled from crops)",
    "tests: 137 (unit + integration), ruff clean",
    "hard-rule tests: no torch / network imports at runtime",
    "ui_resilience_check.py: dropped chunk, dead network, reload",
    "ui_screenshots.py: whole UI run in Chrome, no console errors",
    "ffprobe facts: HEVC Main, 3840x2160, 30 fps, GOP 30, no B-frames",
    "decoder threads 1 / 4 / auto: 138 / 229 / 624 MB (ffmpeg CLI)",
])
arrow([(662, 900), (732, 900)], DIM, dash="6 5")

# ---------------------------------------------------------------- 3. UI protocol
zone(40, 1150, 1495, 545, "3  Live UI protocol (python -m ui, same process as the pipeline)", TEAL, "8 6")
BX, SX = 175, 875
box(65, 1185, 220, 62, "Browser page", ["app.js, no framework, no CDN"])
box(765, 1185, 220, 62, "HTTP server", ["ThreadingHTTPServer, stdlib"])
for x in (BX, SX):
    parts.append(
        f'<line x1="{x}" y1="1247" x2="{x}" y2="1680" stroke="{DIM}" stroke-width="1.3" stroke-dasharray="4 5"/>'
    )
messages = [
    ("->", "GET /  and  /static/app.js, app.css", INK),
    ("->", "GET /api/info  ->  presets, sample clips, 512 MB", INK),
    ("->", "POST /api/jobs {name, size, preset}  ->  {id}  or 409 busy", INK),
    ("->", "GET /api/jobs/{id}/events   SSE, held open", TEAL),
    ("->", "PUT .../data?offset=N   4 MB chunks x 80  ->  {received}", INK),
    ("..", "chunk retried 5x; 409 gives 'received' to resume; 410 run ended", DIM),
    ("<-", "SSE: start, frame, progress, reading, plates, stats", TEAL),
    ("<-", "SSE: done | error | cancelled, then the stream closes", TEAL),
    ("->", "GET /api/jobs/{id}/result.json | result.csv", INK),
    ("->", "POST .../cancel   (Stop button; beacon on tab close)", INK),
    ("->", "GET .../progress  ->  {received, state}   (resync after a failure)", INK),
]
y = 1278
for direction, label, color in messages:
    if direction == "->":
        arrow([(BX + 4, y), (SX - 4, y)], color)
    elif direction == "<-":
        arrow([(SX - 4, y), (BX + 4, y)], color)
    else:
        parts.append(f'<line x1="{BX + 4}" y1="{y}" x2="{SX - 4}" y2="{y}" stroke="{DIM}" stroke-width="1" stroke-dasharray="2 4"/>')
    t((BX + SX) / 2, y - 6, label, 12, 500, color if color != INK else INK, "middle", mono=True)
    if width_of(label, 12, True) > SX - BX - 10:
        warnings.append(f"long message: {label!r}")
    y += 37

s1 = box(1010, 1185, 505, 78, "JobManager", [
    "one run at a time; a new request replaces a run", "whose upload has been silent for 15 s",
])
s2 = box(1010, 1275, 505, 95, "GrowingFile (temp file + condition variable)", [
    "chunks must arrive in order; retried chunks de-duplicated", "a read past the received bytes waits for more",
    "MP4 with moov before mdat: 'not seekable' while uploading",
], "store")
s3 = box(1010, 1382, 505, 62, "Upload watchdog", ["no new bytes for 45 s -> run ended, page told why"])
s4 = box(1010, 1456, 505, 95, "Job event log", [
    "append-only, at most 5,000 events, plain JSON", "only the newest unsent frame is kept",
    "Last-Event-ID: a reconnecting page resumes",
], "store")
s5 = box(1010, 1563, 505, 117, "Runner thread", [
    "pipeline.run(config,", "  source_factory=GrowingFile.reader,", "  observer=job.emit, cancel=job.cancel_event)",
    "temp upload deleted when the run ends",
], mono_lines=True)
arrow([(SX + 4, 1352), (1010, 1224)], INK, "create", at=(948, 1272))
arrow([(SX + 4, 1426), (1010, 1345)], INK, "append", at=(948, 1372))
arrow([(1010, 1500), (SX + 4, 1500)], TEAL, "events", at=(942, 1494), dash="8 5")
arrow([(1515, 1600), (1548, 1600), (1548, 1112)], INK, "runs section 2", at=(1548, 1102))
arrow([(1972, 600), (1985, 600), (1985, 640), (2000, 640), (2000, 1132), (1540, 1132), (1540, 1500), (1517, 1500)],
      TEAL, "job.emit()", at=(1770, 1128), dash="8 5")

# ---------------------------------------------------------------- 4. measured numbers
zone(1575, 1150, 785, 545, "4  Measured on the supplied clips (i9-11950H, 8 cores)", LINE)
t(1600, 1190, "Peak resident memory (MB)", 14, 650)
bars = [
    ("low-mem, command line", 254, TEAL),
    ("balanced, command line", 368, TEAL),
    ("balanced, with the live UI", 399, TEAL),
    ("decoder alone, auto threads", 624, RED),
]
scale = 560 / 650
by = 1206
for label, mb, color in bars:
    t(1600, by + 15, label, 12.5, 500, DIM)
    rect(1790, by + 2, mb * scale, 18, color, color, 0, 4)
    t(1790 + mb * scale + 8, by + 16, f"{mb}", 12.5, 650, color if color == RED else INK)
    by += 30
bx = 1790 + 512 * scale
parts.append(f'<line x1="{bx}" y1="1198" x2="{bx}" y2="{by + 2}" stroke="{RED}" stroke-width="1.6" stroke-dasharray="5 4"/>')
t(bx - 6, by + 18, "512 MB budget", 12.5, 650, RED, "end")

t(1600, 1360, "Latency per call (ms), clip 7378, balanced", 14, 650)
lat = [
    ("Decode, per source frame", 27.8), ("Change gate", 0.3), ("Convert to 4K image", 3.1),
    ("Detector, quick pass", 109.0), ("Detector, deep pass", 300.4), ("OCR, all plates in frame", 88.2),
    ("Tracker update", 0.2), ("Whole frame through models", 267.6),
]
ly = 1376
for label, ms in lat:
    t(1600, ly + 14, label, 12.5, 500, DIM)
    width = max(2, ms / 320 * 360)
    color = INK if label.startswith("Whole") else TEAL
    rect(1820, ly + 3, width, 14, color, color, 0, 3)
    t(1820 + width + 8, ly + 15, f"{ms:g}", 12.5, 650)
    ly += 24
t(1600, ly + 22, "balanced: 13 of 14 readable plates found (93%), 100% precision, no duplicates", 13, 650)
t(1600, ly + 42, "a 60 s clip takes 60 to 78 s; frames the gate skips cost 0.3 ms", 13, 500, DIM)
t(1600, ly + 62, "budget per sampled frame at 5 fps: 200 ms; at 3 fps: 333 ms", 13, 500, DIM)

parts.append("</svg>")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(parts), encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
for w in warnings:
    print("WARN", w)
