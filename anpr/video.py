"""Streaming video reader built on PyAV.

PyAV gives explicit control of decoder threads (the biggest memory lever for 4K HEVC) and
lets us build a tiny grey thumbnail from the decoded frame without paying for a full 4K
BGR conversion on frames the gate will skip.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import av
import numpy as np

log = logging.getLogger(__name__)
T = TypeVar("T")

THUMB_SIZE = (160, 90)  # width, height of the grey thumbnail used by the change gate


@dataclass(frozen=True)
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    codec: str
    frame_count: int


class VideoError(RuntimeError):
    """The video cannot be opened or decoded."""


def _open(source: object) -> av.container.InputContainer:
    """Open a path or an already-open file-like object (e.g. an upload still arriving)."""
    return av.open(source if hasattr(source, "read") else str(source))


def _release(source: object) -> None:
    """Close file-like sources we were handed; paths need nothing."""
    close = getattr(source, "close", None)
    if callable(close):
        close()


def probe(path: str | Path | Any) -> VideoInfo:
    try:
        with _open(path) as container:
            if not container.streams.video:
                raise VideoError(f"no video stream in {path}")
            s = container.streams.video[0]
            fps = float(s.average_rate or s.guessed_rate or 30)
            if container.duration:
                duration = container.duration / av.time_base
            elif s.duration and s.time_base:
                duration = float(s.duration * s.time_base)
            else:
                duration = 0.0
            return VideoInfo(
                path=str(path),
                width=s.codec_context.width,
                height=s.codec_context.height,
                fps=fps,
                duration=float(duration),
                codec=s.codec_context.name,
                frame_count=int(s.frames or 0),
            )
    except av.error.FFmpegError as exc:
        raise VideoError(f"cannot open {path}: {exc}") from exc
    finally:
        _release(path)


class SampledFrame:
    """A frame chosen by the sampling grid. The full-resolution image is made on demand."""

    __slots__ = ("_frame", "index", "thumb", "timestamp")

    def __init__(self, index: int, timestamp: float, thumb: np.ndarray, frame: av.VideoFrame):
        self.index = index
        self.timestamp = timestamp
        self.thumb = thumb  # (90, 160) uint8
        self._frame = frame

    def bgr(self) -> np.ndarray:
        """Full-resolution BGR image (about 25 MB at 4K). Free it as soon as crops are taken."""
        return self._frame.to_ndarray(format="bgr24")


def _timed(frames: Iterator[T], sink: list[float] | None) -> Iterator[T]:
    """Pass frames through, appending the decode time of each one (seconds) to `sink`."""
    if sink is None:
        yield from frames
        return
    iterator = iter(frames)
    while True:
        start = time.perf_counter()
        try:
            frame = next(iterator)
        except StopIteration:
            return
        sink.append(time.perf_counter() - start)
        yield frame


def prefetch(source: Iterator[T], depth: int = 2) -> Iterator[T]:
    """Run `source` in a background thread, keeping up to `depth` items ready.

    Decoding then overlaps with detection. The queue is small on purpose: each queued
    frame holds about 12 MB of decoded 4K video. Exceptions in the producer are re-raised
    in the consumer, and closing the generator early stops the producer.
    """
    ready: queue.Queue = queue.Queue(maxsize=depth)
    stop = threading.Event()
    done = object()

    def put(item: object) -> bool:
        while not stop.is_set():
            try:
                ready.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def work() -> None:
        try:
            for item in source:
                if not put(item):
                    return
            put(done)
        except BaseException as exc:
            put(exc)

    thread = threading.Thread(target=work, name="decoder", daemon=True)
    thread.start()
    try:
        while True:
            item = ready.get()
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while not ready.empty():
            try:
                ready.get_nowait()
            except queue.Empty:
                break
        thread.join(timeout=5.0)


def iter_sampled_frames(
    path: str | Path | Any,
    sample_fps: float,
    decode_threads: int,
    max_seconds: float = 0.0,
    decode_times: list[float] | None = None,
) -> Iterator[SampledFrame]:
    """Decode the whole file but hand out only frames on the sampling grid.

    P-frames depend on earlier frames, so every frame is decoded. Only sampled frames are
    converted to arrays. Timestamps come from the frame's presentation time.
    """
    if sample_fps <= 0:
        raise ValueError("sample_fps must be positive")
    interval = 1.0 / sample_fps
    try:
        container = _open(path)
    except av.error.FFmpegError as exc:
        _release(path)
        raise VideoError(f"cannot open {path}: {exc}") from exc
    try:
        yield from _decode_loop(container, interval, decode_threads, max_seconds, decode_times)
    finally:
        container.close()
        _release(path)


def _decode_loop(
    container: av.container.InputContainer,
    interval: float,
    decode_threads: int,
    max_seconds: float,
    decode_times: list[float] | None,
) -> Iterator[SampledFrame]:
    next_t = 0.0
    with container:
        stream = container.streams.video[0]
        stream.thread_type = "FRAME"
        stream.codec_context.thread_count = max(1, decode_threads)
        fps = float(stream.average_rate or 30)
        for index, frame in enumerate(_timed(container.decode(stream), decode_times)):
            if frame.pts is not None and frame.time_base is not None:
                t = float(frame.pts * frame.time_base)
            else:
                t = index / fps
            if max_seconds > 0 and t > max_seconds:
                return
            if t + 1e-6 < next_t:
                continue
            while next_t <= t + 1e-6:
                next_t += interval
            thumb = frame.reformat(
                width=THUMB_SIZE[0], height=THUMB_SIZE[1], format="gray", interpolation="AREA"
            ).to_ndarray()
            yield SampledFrame(index, t, thumb, frame)
