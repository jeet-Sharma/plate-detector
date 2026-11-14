"""One-time model download. Needs internet; the main program never does.

    python scripts/setup_models.py

Files go to models/ and are verified against the SHA-256 values below. Existing files with
the right hash are kept.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

MODELS = [
    {
        "file": "yolo-v9-t-640-license-plates-end2end.onnx",
        "url": "https://github.com/ankandrew/open-image-models/releases/download/assets/"
        "yolo-v9-t-640-license-plates-end2end.onnx",
        "sha256": "c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076",
        "what": "plate detector, YOLOv9-t, 640 input, NMS inside the graph (7.8 MB)",
    },
    {
        "file": "cct_s_v2_global.onnx",
        "url": "https://github.com/ankandrew/cnn-ocr-lp/releases/download/arg-plates/"
        "cct_s_v2_global.onnx",
        "sha256": "384bbbd2cea3ef54761d3df70822ef3a349ee1a112aeafddbe0e3ba06bc6e47b",
        "what": "plate OCR, fast-plate-ocr cct-s-v2-global, 64x128 input (5.3 MB)",
    },
    {
        "file": "cct_s_v2_global_plate_config.yaml",
        "url": "https://github.com/ankandrew/cnn-ocr-lp/releases/download/arg-plates/"
        "cct_s_v2_global_plate_config.yaml",
        "sha256": "0335c74a305173bb6f393efed0fde03cadeaa0b649ed8e19f431016d8232d0a6",
        "what": "OCR alphabet and input size",
    },
]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    MODELS_DIR.mkdir(exist_ok=True)
    failed = False
    for item in MODELS:
        target = MODELS_DIR / item["file"]
        if target.exists() and sha256_of(target) == item["sha256"]:
            print(f"ok       {item['file']}")
            continue
        print(f"download {item['file']}  ({item['what']})")
        urllib.request.urlretrieve(item["url"], target)
        if sha256_of(target) != item["sha256"]:
            print(f"HASH MISMATCH for {item['file']}; delete it and retry", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
