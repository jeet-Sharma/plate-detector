"""Runs one job: the recognition pipeline in a worker thread, reporting to the job's log."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from anpr.config import build_config
from anpr.output import plate_row
from anpr.pipeline import Cancelled, run
from ui.jobs import UPLOAD_STALL_SECONDS, Job, JobManager

log = logging.getLogger(__name__)


def start_job(manager: JobManager, job: Job) -> threading.Thread:
    thread = threading.Thread(target=_work, args=(manager, job), name=f"job-{job.id}", daemon=True)
    thread.start()
    threading.Thread(target=_watch_upload, args=(job,), name=f"watch-{job.id}", daemon=True).start()
    return thread


def _watch_upload(job: Job) -> None:
    """Free the slot if the upload dies (closed tab, network drop, browser error)."""
    while not job.finished:
        if job.stalled(UPLOAD_STALL_SECONDS):
            log.warning("job %s: upload stalled at %d bytes", job.id, job.source.received)
            job.fail_stalled()
            return
        time.sleep(1.0)


def _work(manager: JobManager, job: Job) -> None:
    try:
        video_path = job.sample_path if job.sample_path is not None else Path(job.name)
        config = build_config(video_path, job.preset, out_dir=job.out_dir)
        factory = job.source.reader if job.source is not None else None

        def observer(kind: str, data: dict[str, Any]) -> None:
            job.emit(kind, data)

        summary = run(config, source_factory=factory, observer=observer, cancel=job.cancel_event)
        job.emit(
            "done",
            {
                "plates": [plate_row(p) for p in summary.plates],
                "run": _brief(summary.run),
                "files": {"json": "result.json", "csv": "result.csv"},
            },
        )
    except Cancelled:
        if not job.finished:
            job.emit("cancelled", {"message": "Stopped early. Plates found so far are kept."})
    except Exception as exc:
        if job.finished:  # already reported, for example a stalled upload
            return
        if job.cancel_event.is_set():
            job.emit("cancelled", {"message": "Stopped early."})
        else:
            log.exception("job %s failed", job.id)
            job.emit("error", {"message": _friendly(exc)})
    finally:
        manager.cleanup(job)


def _friendly(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "invalid data" in lowered or "no video stream" in lowered or "cannot open" in lowered:
        return "This file could not be read as a video. Try an MP4 recorded by a dashcam."
    if "model file not found" in lowered:
        return "Model files are missing. Run: python scripts/setup_models.py"
    return text


def _brief(run_info: dict[str, Any]) -> dict[str, Any]:
    """The parts of the run report the page shows after a run."""
    return {
        "timing_s": run_info["timing_s"],
        "memory": run_info["memory"],
        "frames": run_info["frames"],
        "throughput": run_info["throughput"],
        "latency_ms": run_info["latency_ms"],
        "preset": run_info["preset"],
    }
