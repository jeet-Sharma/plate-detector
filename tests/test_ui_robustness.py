"""A dead or flaky upload must never leave the UI stuck or lose data."""

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from ui.jobs import Busy, JobManager
from ui.server import serve

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.unit


@pytest.fixture
def server(tmp_path):
    srv = serve("127.0.0.1", 0, ROOT, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Length": str(len(body))} if body is not None else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read() or b"{}")
    conn.close()
    return response.status, data


def make_job(port, size=10):
    body = json.dumps({"name": "a.mp4", "size": size}).encode()
    status, data = call(port, "POST", "/api/jobs", body)
    assert status == 200
    return data["id"]


def test_a_retried_chunk_is_accepted_without_duplicating_data(server):
    job_id = make_job(server)
    assert call(server, "PUT", f"/api/jobs/{job_id}/data?offset=0", b"abcd")[1]["received"] == 4
    # the page did not see the reply and sends the same chunk again, now with more bytes
    status, data = call(server, "PUT", f"/api/jobs/{job_id}/data?offset=0", b"abcdefg")
    assert status == 200 and data["received"] == 7
    status, data = call(server, "GET", f"/api/jobs/{job_id}/progress")
    assert data["received"] == 7
    call(server, "POST", f"/api/jobs/{job_id}/cancel", b"")


def test_a_gap_tells_the_page_where_to_resume(server):
    job_id = make_job(server)
    call(server, "PUT", f"/api/jobs/{job_id}/data?offset=0", b"abcd")
    status, data = call(server, "PUT", f"/api/jobs/{job_id}/data?offset=8", b"zz")
    assert status == 409 and data["received"] == 4
    call(server, "POST", f"/api/jobs/{job_id}/cancel", b"")


def test_uploading_to_a_run_that_has_ended_says_so(server):
    job_id = make_job(server)
    call(server, "POST", f"/api/jobs/{job_id}/cancel", b"")
    time.sleep(0.5)
    status, data = call(server, "PUT", f"/api/jobs/{job_id}/data?offset=0", b"abcd")
    assert status == 410 and "ended" in data["error"]


def test_an_upload_that_stops_does_not_block_the_next_run(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    stuck = manager.create("a.mp4", "balanced", size=1000)
    stuck.source.append(0, b"x" * 100)
    with pytest.raises(Busy):
        manager.create("b.mp4", "balanced", size=10)  # still fresh: genuinely busy
    stuck.source._last_activity -= 60  # the upload has been silent for a minute
    replacement = manager.create("b.mp4", "balanced", size=10)
    assert replacement.id != stuck.id
    assert stuck.state == "error"
    events = stuck.wait_events(0, timeout=0.1)
    assert "stopped arriving" in events[-1].data["message"]


def test_a_finished_upload_is_never_treated_as_stalled(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create("a.mp4", "balanced", size=4)
    job.source.append(0, b"abcd")
    job.source._last_activity -= 600
    assert not job.stalled(15.0)


def test_sample_runs_are_never_treated_as_stalled(tmp_path):
    manager = JobManager(tmp_path / "up", tmp_path / "out")
    job = manager.create("s.mp4", "balanced", sample_path=ROOT / "README.md")
    assert not job.stalled(0.0)
