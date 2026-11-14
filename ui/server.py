"""Local HTTP server for the live UI. Standard library only; binds to 127.0.0.1 by default."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from anpr import __version__
from anpr.config import PRESETS
from ui.jobs import Busy, Job, JobManager
from ui.runner import start_job

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
MAX_UPLOAD = 8 * 1024**3
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


class App:
    def __init__(self, root: Path, work_dir: Path, samples_dir: Path | None = None) -> None:
        self.root = root
        self.samples_dir = samples_dir or root
        self.manager = JobManager(work_dir / "uploads", root / "output" / "ui")

    def _videos(self) -> list[Path]:
        if not self.samples_dir.is_dir():
            return []
        return sorted(p for p in self.samples_dir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)

    def samples(self) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "size_mb": round(p.stat().st_size / 1024**2, 1)}
            for p in self._videos()
        ]

    def sample_path(self, name: str) -> Path | None:
        for item in self._videos():
            if item.name == name and item.suffix.lower() in VIDEO_SUFFIXES:
                return item
        return None


def _plain(value: Any) -> Any:
    """JSON fallback: numpy numbers (and anything else with .item()) become plain numbers."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PlateStream/" + __version__

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            log.debug("%s " + fmt, self.address_string(), *args)

        # ---- helpers -------------------------------------------------------------

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str, **extra: Any) -> None:
            if status >= 400 and status != 404:
                log.warning("%s %s -> %d %s", self.command, self.path[:80], status, message)
            self._json({"error": message, **extra}, status)

        def _body_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 65536:
                return {}
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def _job(self, job_id: str) -> Job | None:
            return app.manager.get(job_id) if JOB_ID.match(job_id) else None

        # ---- routing -------------------------------------------------------------

        def do_GET(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if url.path == "/api/info":
                return self._json(
                    {
                        "version": __version__,
                        "presets": list(PRESETS),
                        "samples": app.samples(),
                        "budget_mb": 512,
                    }
                )
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "progress":
                job = self._job(parts[2])
                if not job:
                    return self._error(404, "no job")
                have = job.source.received if job.source is not None else 0
                return self._json({"received": have, "state": job.state})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "events":
                job = self._job(parts[2])
                return self._events(job, parse_qs(url.query)) if job else self._error(404, "no job")
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] in (
                "result.json",
                "result.csv",
            ):
                job = self._job(parts[2])
                return self._download(job, parts[3]) if job else self._error(404, "no job")
            return self._static(url.path)

        def do_POST(self) -> None:
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            if parts == ["api", "jobs"]:
                return self._create_job()
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                job = self._job(parts[2])
                if not job:
                    return self._error(404, "no job")
                job.cancel()
                return self._json({"ok": True})
            self._error(404, "not found")

        def do_PUT(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "data":
                job = self._job(parts[2])
                if not job or job.source is None:
                    return self._error(404, "no upload for this job")
                return self._receive(job, parse_qs(url.query))
            self._error(404, "not found")

        # ---- handlers ------------------------------------------------------------

        def _create_job(self) -> None:
            body = self._body_json()
            preset = body.get("preset") or "balanced"
            if preset not in PRESETS:
                return self._error(400, f"unknown preset {preset!r}")
            try:
                sample = body.get("sample")
                if sample:
                    path = app.sample_path(str(sample))
                    if path is None:
                        return self._error(404, "unknown sample clip")
                    job = app.manager.create(path.name, preset, sample_path=path)
                else:
                    name = str(body.get("name") or "video.mp4")
                    size = int(body.get("size") or 0)
                    if not 0 < size <= MAX_UPLOAD:
                        return self._error(400, "size must be between 1 byte and 8 GB")
                    job = app.manager.create(name, preset, size=size)
            except Busy as exc:
                return self._error(409, str(exc))
            except (ValueError, TypeError) as exc:
                return self._error(400, str(exc))
            start_job(app.manager, job)
            self._json({"id": job.id, "name": job.name})

        def _receive(self, job: Job, query: dict[str, list[str]]) -> None:
            assert job.source is not None
            if job.finished:
                return self._error(410, "this run has already ended")
            try:
                offset = int(query.get("offset", ["0"])[0])
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(400, "bad offset or length")
            if length <= 0:
                return self._error(411, "Content-Length required")
            source = job.source
            if offset > source.received:  # a gap: the page must resend from `received`
                self.rfile.read(length)
                have = source.received
                return self._error(409, f"expected offset {have}, got {offset}", received=have)
            skip = source.received - offset  # a retried chunk: keep only the bytes we lack
            try:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        log.warning("job %s: connection closed mid-chunk", job.id)
                        return
                    remaining -= len(chunk)
                    if skip:
                        drop = min(skip, len(chunk))
                        chunk, skip = chunk[drop:], skip - drop
                    if chunk:
                        source.append(source.received, chunk)
            except (ValueError, OSError) as exc:
                return self._error(409, str(exc), received=source.received)
            self._json({"received": source.received, "total": source.total})

        def _events(self, job: Job, query: dict[str, list[str]]) -> None:
            last = int(self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0] or 0)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while True:
                    batch = job.wait_events(last, timeout=15.0)
                    if not batch:
                        if job.finished:
                            return
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    for event in batch:
                        payload = json.dumps(event.data, separators=(",", ":"), default=_plain)
                        self.wfile.write(
                            f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n".encode()
                        )
                        last = event.seq
                    self.wfile.flush()
                    if batch[-1].type in ("done", "error", "cancelled"):
                        return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _download(self, job: Job, kind: str) -> None:
            suffix = "json" if kind.endswith("json") else "csv"
            path = job.out_dir / f"{job.result_stem}.plates.{suffix}"
            if not path.exists():
                return self._error(404, "result not ready")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json" if suffix == "json" else "text/csv"
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{job.result_stem}.plates.{suffix}"'
            )
            self.end_headers()
            self.wfile.write(data)

        def _static(self, url_path: str) -> None:
            rel = "index.html" if url_path in ("/", "") else url_path.removeprefix("/static/")
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                return self._error(404, "not found")
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            textual = ctype.startswith("text") or ctype.endswith("javascript")
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if textual else ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(
    host: str, port: int, root: Path, work_dir: Path, samples_dir: Path | None = None
) -> ThreadingHTTPServer:
    app = App(root, work_dir, samples_dir)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
