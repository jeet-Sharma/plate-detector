"""End-to-end checks. Skipped when model files (or the sample video) are missing."""

import csv
import json
from pathlib import Path

import av
import numpy as np
import pytest

from anpr.config import DETECTOR_FILE, MODELS_DIR, OCR_CONFIG_FILE, OCR_FILE, build_config
from anpr.ort_utils import ModelMissingError
from anpr.pipeline import run
from anpr.video import VideoError

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "REC20261007-174724-7378.mp4"
MODELS_PRESENT = all((MODELS_DIR / f).exists() for f in (DETECTOR_FILE, OCR_FILE, OCR_CONFIG_FILE))
needs_models = pytest.mark.skipif(not MODELS_PRESENT, reason="run scripts/setup_models.py")
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason="sample video not present")


def make_blank_video(path: Path, seconds: int = 1, fps: int = 10) -> None:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for _ in range(seconds * fps):
            frame = av.VideoFrame.from_ndarray(np.full((240, 320, 3), 90, np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_missing_video_raises_a_clear_error(tmp_path):
    with pytest.raises(VideoError, match="not found"):
        run(build_config(tmp_path / "nope.mp4", out_dir=tmp_path))


@needs_models
def test_video_without_plates_gives_empty_but_valid_output(tmp_path):
    video = tmp_path / "blank.mp4"
    make_blank_video(video)
    summary = run(build_config(video, out_dir=tmp_path / "out"))
    assert summary.plates == []
    assert json.loads(summary.json_path.read_text())["plates"] == []
    with summary.csv_path.open() as fh:
        assert list(csv.DictReader(fh)) == []


@needs_models
def test_missing_model_file_names_the_setup_script(tmp_path):
    video = tmp_path / "blank.mp4"
    make_blank_video(video)
    config = build_config(video, out_dir=tmp_path / "out", models_dir=tmp_path / "empty")
    with pytest.raises(ModelMissingError, match="setup_models"):
        run(config)


@needs_models
@needs_sample
def test_first_seconds_of_clip_2_find_the_tesla_plate_within_budget(tmp_path):
    config = build_config(SAMPLE, out_dir=tmp_path, max_seconds=8)
    summary = run(config)
    plates = {p.plate for p in summary.plates}
    assert "9KTA262" in plates
    assert summary.run["memory"]["peak_rss_mb"] < 512
    data = json.loads(summary.json_path.read_text())
    assert {"plate", "first_seen", "last_seen", "confidence", "readings"} <= set(data["plates"][0])
