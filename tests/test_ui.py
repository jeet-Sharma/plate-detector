"""Live UI: growing-file reader, event log, HTTP routes, and streaming while uploading."""

import http.client
import json
import threading
import time
from pathlib import Path

import av
import numpy as np
import pytest

from anpr.config import DETECTOR_FILE, MODELS_DIR, OCR_CONFIG_FILE, OCR_FILE
from ui.growing_file import GrowingFile, UploadError
from ui.jobs import Busy, JobManager, safe_name
from ui.runner import start_job
from ui.server import serve

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "REC20261007-174724-7378.mp4"
MODELS_PRESENT = all((MODELS_DIR / f).exists() for f in (DETECTOR_FILE, OCR_FILE, OCR_CONFIG_FILE))
needs_models = pytest.mark.skipif(not MODELS_PRESENT, reason="run scripts/setup_models.py")
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason="sample video not present")


# ------------------------------------------------------------------ GrowingFile
@pytest.mark.unit
def test_reader_gets_bytes_as_they_arrive(tmp_path):
    gf = GrowingFile(tmp_path / "f.bin", 10)
    reader = gf.reader()
    gf.append(0, b"hello")
    assert reader.read(5) == b"hello"

    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("data", reader.read(5)))
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()  # waiting for the rest of the upload
    gf.append(5, b"world")
    thread.join(timeout=3)
    assert result["data"] == b"world"
    assert reader.read(5) == b""  # end of file


@pytest.mark.unit
def test_seek_end_uses_the_declared_size_and_readers_are_independent(tmp_path):
    gf = GrowingFile(tmp_path / "f.bin", 8)
    gf.append(0, b"ABCDEFGH")
    a, b = gf.reader(), gf.reader()
    assert a.seek(-3, 2) == 5
    assert a.read(3) == b"FGH"
    assert b.read(2) == b"AB"
    assert b.tell() == 2


@pytest.mark.unit
def test_chunks_must_arrive_in_order_and_within_the_declared_size(tmp_path):
    gf = GrowingFile(tmp_path / "f.bin", 6)
    gf.append(0, b"abc")
    with pytest.raises(ValueError):
        gf.append(0, b"xyz")  # wrong offset
    with pytest.raises(ValueError):
        gf.append(3, b"toolong!")  # more than declared


@pytest.mark.unit
def test_failing_the_upload_wakes_a_waiting_reader(tmp_path):
    gf = GrowingFile(tmp_path / "f.bin", 100)
    reader = gf.reader()
    errors = []

    def wait():
        try:
            reader.read(10)
        except UploadError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(0.2)
    gf.fail("cancelled")
    thread.join(timeout=3)
    assert errors == ["cancelled"]


# ------------------------------------------------------------------ job log
@pytest.mark.unit
def test_event_log_keeps_only_the_newest_frame(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create("clip.mp4", "balanced", size=10)
    job.emit("start", {"a": 1})
    for i in range(5):
        job.emit("frame", {"n": i})
    job.emit("plates", {"plates": []})
    events = job.wait_events(0, timeout=0.1)
    assert [e.type for e in events] == ["start", "frame", "plates"]
    assert [e.data for e in events if e.type == "frame"] == [{"n": 4}]
    assert job.wait_events(events[-1].seq, timeout=0.05) == []


@pytest.mark.unit
def test_resume_after_a_sequence_number_and_terminal_state(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create("clip.mp4", "balanced", size=10)
    job.emit("start", {})
    first = job.wait_events(0, timeout=0.1)[0].seq
    job.emit("done", {"plates": []})
    later = job.wait_events(first, timeout=0.1)
    assert [e.type for e in later] == ["done"]
    assert job.finished


@pytest.mark.unit
def test_only_one_job_runs_at_a_time(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    first = manager.create("a.mp4", "balanced", size=10)
    with pytest.raises(Busy):
        manager.create("b.mp4", "balanced", size=10)
    first.emit("done", {})
    assert manager.create("b.mp4", "balanced", size=10)


@pytest.mark.unit
def test_uploaded_names_are_reduced_to_safe_characters():
    assert safe_name("..\\..\\evil name?.mp4") == "evil_name_.mp4"
    assert safe_name("C:/clips/trip 1.mp4") == "trip_1.mp4"
    assert safe_name("") == "video"


# ------------------------------------------------------------------ HTTP routes
@pytest.fixture
def running_server(tmp_path):
    server = serve("127.0.0.1", 0, ROOT, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, response.getheader("Content-Type", ""), data


@pytest.mark.unit
def test_info_lists_presets_and_samples(running_server):
    status, ctype, data = request(running_server, "GET", "/api/info")
    info = json.loads(data)
    assert status == 200 and "json" in ctype
    assert set(info["presets"]) == {"low-mem", "balanced", "fast"}
    assert info["budget_mb"] == 512


@pytest.mark.unit
def test_index_and_assets_are_served_with_the_right_types(running_server):
    status, ctype, data = request(running_server, "GET", "/")
    assert status == 200 and ctype.startswith("text/html") and b"Plate Stream" in data
    assert request(running_server, "GET", "/static/app.js")[0] == 200
    assert request(running_server, "GET", "/static/app.css")[1].startswith("text/css")


@pytest.mark.unit
def test_path_traversal_and_unknown_routes_are_refused(running_server):
    assert request(running_server, "GET", "/static/../pyproject.toml")[0] == 404
    assert request(running_server, "GET", "/static/%2e%2e/pyproject.toml")[0] == 404
    assert request(running_server, "GET", "/nope")[0] == 404
    assert request(running_server, "GET", "/api/jobs/not-a-job/events")[0] == 404


@pytest.mark.unit
def test_bad_job_requests_get_clear_errors(running_server):
    def post(payload):
        body = json.dumps(payload).encode()
        headers = {"Content-Length": str(len(body))}
        return request(running_server, "POST", "/api/jobs", body, headers)

    assert post({"preset": "turbo", "name": "a.mp4", "size": 5})[0] == 400
    assert post({"name": "a.mp4", "size": 0})[0] == 400
    assert post({"sample": "../secret.mp4"})[0] == 404


@pytest.mark.unit
def test_upload_chunks_must_follow_the_offset(running_server):
    body = json.dumps({"name": "a.mp4", "size": 10}).encode()
    headers = {"Content-Length": str(len(body))}
    status, _, data = request(running_server, "POST", "/api/jobs", body, headers)
    assert status == 200
    job_id = json.loads(data)["id"]
    path = f"/api/jobs/{job_id}/data?offset=5"
    status, _, data = request(running_server, "PUT", path, b"abc", {"Content-Length": "3"})
    assert status == 409
    request(running_server, "POST", f"/api/jobs/{job_id}/cancel", b"", {"Content-Length": "0"})


# ------------------------------------------------------------------ with models
def make_blank_video(path: Path) -> None:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for _ in range(10):
            frame = av.VideoFrame.from_ndarray(np.full((240, 320, 3), 90, np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def collect(job, timeout=60.0):
    """Read the job's events until it reaches a terminal state."""
    seen, last, deadline = [], 0, time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in job.wait_events(last, timeout=1.0):
            seen.append(event)
            last = event.seq
        if job.finished and not job.wait_events(last, timeout=0.05):
            return seen
    raise AssertionError("job did not finish: " + str([e.type for e in seen][-5:]))


@pytest.mark.integration
@needs_models
def test_a_whole_upload_runs_to_done_with_files_to_download(tmp_path):
    video = tmp_path / "blank.mp4"
    make_blank_video(video)
    data = video.read_bytes()
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create("blank.mp4", "balanced", size=len(data))
    start_job(manager, job)
    half = len(data) // 2
    job.source.append(0, data[:half])
    job.source.append(half, data[half:])
    events = collect(job)
    types = [e.type for e in events]
    assert types[0] == "start" and types[-1] == "done"
    done = events[-1].data
    assert done["plates"] == []
    assert "latency_ms" in done["run"]
    json.dumps([e.data for e in events])  # every event must be plain JSON, as SSE sends it
    assert (job.out_dir / "blank.plates.json").exists()


@pytest.mark.integration
@needs_models
@needs_sample
def test_analysis_starts_while_the_upload_is_still_arriving(tmp_path):
    data = SAMPLE.read_bytes()
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create(SAMPLE.name, "balanced", size=len(data))
    start_job(manager, job)
    sent, step = 0, 4 * 1024 * 1024
    seen_frame = False
    deadline = time.monotonic() + 90
    while sent < 60 * 1024 * 1024 and time.monotonic() < deadline and not seen_frame:
        job.source.append(sent, data[sent : sent + step])
        sent += step
        seen_frame = any(e.type == "frame" for e in job.wait_events(0, timeout=0.3))
    while not seen_frame and time.monotonic() < deadline:
        seen_frame = any(e.type == "frame" for e in job.wait_events(0, timeout=0.5))
    assert seen_frame, "no frame event before the upload finished"
    assert not job.source.complete  # most of the file has not been sent yet
    job.cancel()
    events = collect(job, timeout=30)
    assert events[-1].type == "cancelled"


# ------------------------------------------------------------------ fast-start detection
def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


@pytest.mark.unit
def test_index_first_mp4_streams_while_uploading_and_seeks_once_complete(tmp_path):
    data = box(b"ftyp", b"isom0000") + box(b"moov", b"m" * 40) + box(b"mdat", b"x" * 200)
    gf = GrowingFile(tmp_path / "a.mp4", len(data))
    gf.append(0, data[:100])
    assert gf.faststart() is True
    assert gf.reader().seekable() is False  # streaming mode while bytes are still arriving
    gf.append(100, data[100:])
    assert gf.reader().seekable() is True


@pytest.mark.unit
def test_index_at_the_end_must_wait_for_the_whole_upload(tmp_path):
    data = box(b"ftyp", b"isom0000") + box(b"mdat", b"x" * 200) + box(b"moov", b"m" * 40)
    gf = GrowingFile(tmp_path / "b.mp4", len(data))
    gf.append(0, data[:120])
    assert gf.faststart() is False
    assert gf.reader().seekable() is True  # seeks to the end, which waits for the rest


@pytest.mark.unit
def test_other_containers_are_treated_as_needing_the_full_file(tmp_path):
    data = b"RIFF" + b"\x00" * 200
    gf = GrowingFile(tmp_path / "c.avi", len(data))
    gf.append(0, data[:60])
    assert gf.faststart() is False


@pytest.mark.integration
@needs_models
@needs_sample
def test_every_live_event_from_a_real_clip_is_plain_json(tmp_path):
    """Regression: numpy numbers in a frame event once broke the event stream."""
    data = SAMPLE.read_bytes()
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create(SAMPLE.name, "balanced", size=len(data))
    start_job(manager, job)
    sent, step = 0, 4 * 1024 * 1024
    types: set[str] = set()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not {"frame", "reading", "plates", "stats"} <= types:
        if sent < len(data):
            job.source.append(sent, data[sent : sent + step])
            sent += step
        for event in job.wait_events(0, timeout=0.3):
            json.dumps(event.data)
            types.add(event.type)
    job.cancel()
    assert {"frame", "reading", "plates", "stats"} <= types
