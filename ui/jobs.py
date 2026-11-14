"""Jobs: one analysis run with an append-only event log that browsers read over SSE."""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ui.growing_file import GrowingFile

MAX_LOG_EVENTS = 5000
UPLOAD_STALL_SECONDS = 45.0  # an upload with no new bytes for this long is given up on
REPLACE_STALLED_AFTER = 15.0  # a new request may replace a run whose upload was idle this long
TERMINAL = {"done", "error", "cancelled"}


class Busy(RuntimeError):
    """Another job is still running."""


@dataclass(frozen=True)
class Event:
    seq: int
    type: str
    data: dict[str, Any]


def safe_name(name: str) -> str:
    """File-name part of an uploaded name, reduced to letters, digits, dot, dash, underscore."""
    base = re.split(r"[\\/]", name)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "video"
    return cleaned[:80]


@dataclass
class Job:
    id: str
    name: str
    preset: str
    out_dir: Path
    source: GrowingFile | None = None  # None for sample clips read from disk
    sample_path: Path | None = None
    state: str = "running"
    created: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result_stem: str = ""

    def __post_init__(self) -> None:
        self._log: list[Event] = []
        self._frame: Event | None = None
        self._seq = 0
        self._cond = threading.Condition()

    # ---- producing events --------------------------------------------------------

    def emit(self, type_: str, data: dict[str, Any]) -> None:
        """Record an event. Frames are never queued: only the newest one is kept, so a slow
        browser cannot build a backlog of 60 KB images."""
        with self._cond:
            self._seq += 1
            event = Event(self._seq, type_, data)
            if type_ == "frame":
                self._frame = event
            elif len(self._log) < MAX_LOG_EVENTS or type_ in TERMINAL:
                self._log.append(event)
            if type_ in TERMINAL:
                self.state = type_
            self._cond.notify_all()

    # ---- consuming events --------------------------------------------------------

    def wait_events(self, after: int, timeout: float) -> list[Event]:
        """Events with a sequence number above `after`, waiting up to `timeout` seconds."""
        with self._cond:
            deadline = time.monotonic() + timeout
            while True:
                fresh = [e for e in self._log if e.seq > after]
                if self._frame is not None and self._frame.seq > after:
                    fresh.append(self._frame)
                if fresh:
                    return sorted(fresh, key=lambda e: e.seq)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.state in TERMINAL:
                    return []
                self._cond.wait(timeout=remaining)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL

    def stalled(self, limit: float) -> bool:
        """True while running with an upload that has been silent for `limit` seconds."""
        return (
            self.state == "running"
            and self.source is not None
            and not self.source.complete
            and self.source.idle_seconds > limit
        )

    def fail_stalled(self) -> None:
        """End a run whose upload stopped arriving, and tell any page still listening."""
        if self.finished:
            return
        self.emit(
            "error",
            {"message": "The upload stopped arriving, so this run was ended. Please try again."},
        )
        self.cancel_event.set()
        if self.source is not None:
            self.source.fail("upload stalled")

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.source is not None:
            self.source.fail("cancelled")


class JobManager:
    """Holds jobs. Only one may run at a time (memory budget)."""

    def __init__(self, upload_dir: Path, out_root: Path) -> None:
        self.upload_dir = upload_dir
        self.out_root = out_root
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        upload_dir.mkdir(parents=True, exist_ok=True)
        out_root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        name: str,
        preset: str,
        size: int | None = None,
        sample_path: Path | None = None,
    ) -> Job:
        with self._lock:
            for old in self._jobs.values():  # a dead upload must not block the next one
                if old.stalled(REPLACE_STALLED_AFTER):
                    old.fail_stalled()
            if any(not j.finished for j in self._jobs.values()):
                raise Busy("another clip is still being analysed; try again when it finishes")
            job_id = secrets.token_urlsafe(9)
            clean = safe_name(name)
            source = None
            if sample_path is None:
                if not size or size <= 0:
                    raise ValueError("size is required for uploads")
                source = GrowingFile(self.upload_dir / f"{job_id}-{clean}", size)
            job = Job(
                id=job_id,
                name=clean,
                preset=preset,
                out_dir=self.out_root / job_id,
                source=source,
                sample_path=sample_path,
                result_stem=Path(clean).stem,
            )
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cleanup(self, job: Job) -> None:
        """Delete the uploaded temporary file once a run has ended."""
        if job.source is not None:
            job.source.discard()
