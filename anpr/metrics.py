"""Stage timing and peak memory measurement."""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Iterator

import psutil

MB = 1024 * 1024


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending list (0 for an empty list)."""
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


class StageTimers:
    """Accumulate wall-clock seconds per named stage, and keep every individual duration so
    per-call latency (mean, median, 95th percentile, maximum) can be reported."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self.samples: dict[str, list[float]] = {}

    def record(self, name: str, duration: float) -> None:
        self.seconds[name] = self.seconds.get(name, 0.0) + duration
        self.samples.setdefault(name, []).append(duration)

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - start)

    def timed_iter(self, name: str, iterator: Iterator) -> Iterator:
        """Time each ``next()`` call of `iterator` (e.g. the decoder) under `name`."""
        while True:
            start = time.perf_counter()
            try:
                item = next(iterator)
            except StopIteration:
                self.record(name, time.perf_counter() - start)
                return
            self.record(name, time.perf_counter() - start)
            yield item

    def latency_ms(self, extra: dict[str, list[float]] | None = None) -> dict[str, dict]:
        """Per-call latency in milliseconds for every stage that was timed."""
        merged = {**self.samples, **(extra or {})}
        out: dict[str, dict] = {}
        for name, values in merged.items():
            ordered = sorted(values)
            out[name] = {
                "calls": len(ordered),
                "mean_ms": round(1000 * sum(ordered) / len(ordered), 2),
                "p50_ms": round(1000 * percentile(ordered, 0.50), 2),
                "p95_ms": round(1000 * percentile(ordered, 0.95), 2),
                "max_ms": round(1000 * ordered[-1], 2),
            }
        return out


class MemoryMonitor:
    """Sample resident memory in a background thread to catch the high-water mark.

    The OS peak counters (``peak_wset`` on Windows, ``VmHWM`` on Linux) are read at the end
    and take precedence, because sampling can miss a short spike.
    """

    def __init__(self, interval: float = 0.05) -> None:
        self._proc = psutil.Process()
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.sampled_peak_rss = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = self._proc.memory_info().rss
            self.sampled_peak_rss = max(self.sampled_peak_rss, rss)
            self._stop.wait(self._interval)

    def __enter__(self) -> MemoryMonitor:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def report(self) -> dict[str, float | str | None]:
        """Peak resident memory and, where the OS exposes it, peak private (commit) memory."""
        info = self._proc.memory_info()
        peak_private: float | None = None
        source = "sampled rss"
        peak_rss = float(self.sampled_peak_rss)
        if sys.platform == "win32":
            peak_rss = max(peak_rss, float(info.peak_wset))
            peak_private = info.peak_pagefile / MB
            source = "windows peak_wset"
        elif sys.platform.startswith("linux"):
            hwm = _linux_status_kb("VmHWM")
            if hwm is not None:
                peak_rss = max(peak_rss, hwm * 1024.0)
                source = "linux VmHWM"
        else:
            import resource

            rss_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_rss = max(peak_rss, float(rss_max))  # bytes on macOS
            source = "macos ru_maxrss"
        return {
            "peak_rss_mb": round(peak_rss / MB, 1),
            "peak_private_mb": round(peak_private, 1) if peak_private is not None else None,
            "source": source,
        }


def _linux_status_kb(key: str) -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def current_rss_mb() -> float:
    """Resident memory of this process right now, in megabytes."""
    return round(psutil.Process().memory_info().rss / MB, 1)
