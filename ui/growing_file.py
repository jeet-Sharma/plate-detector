"""A file that is still being uploaded, readable while it grows.

The writer appends chunks to a temporary file. Readers behave like a normal seekable binary
file, except that a read beyond the bytes received so far waits for more. Seeking to the end
uses the size declared up front, so a video whose index sits at the end of the file simply
waits for the upload to finish, while one with its index at the start (both supplied clips)
can be decoded while the upload is in progress.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path


class UploadError(OSError):
    """The upload failed or was cancelled while a reader was waiting."""


class GrowingFile:
    def __init__(self, path: Path, total_size: int) -> None:
        self.path = path
        self.total = total_size
        self.received = 0
        self._cond = threading.Condition()
        self._finished = False
        self._error: str | None = None
        self._faststart: bool | None = None
        self._last_activity = time.monotonic()
        self._writer = path.open("wb")

    # ---- writer side -------------------------------------------------------------

    def append(self, offset: int, data: bytes) -> int:
        """Add `data` at `offset`. Chunks must arrive in order. Returns bytes received."""
        with self._cond:
            if offset != self.received:
                raise ValueError(f"expected offset {self.received}, got {offset}")
            if self.received + len(data) > self.total:
                raise ValueError("more data than the declared size")
            self._writer.write(data)
            self._writer.flush()
            self.received += len(data)
            self._last_activity = time.monotonic()
            if self.received >= self.total:
                self._finish_locked()
            self._cond.notify_all()
            return self.received

    def _finish_locked(self) -> None:
        self._finished = True
        self._writer.close()

    def fail(self, reason: str) -> None:
        """Wake every waiting reader with an error."""
        with self._cond:
            self._error = reason
            if not self._writer.closed:
                self._writer.close()
            self._cond.notify_all()

    @property
    def complete(self) -> bool:
        return self._finished

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last bytes arrived (0 once the upload is complete or failed)."""
        if self._finished or self._error:
            return 0.0
        return time.monotonic() - self._last_activity

    def discard(self) -> None:
        self.fail(self._error or "discarded")
        with contextlib.suppress(OSError):
            os.remove(self.path)

    # ---- reader side -------------------------------------------------------------

    def reader(self) -> GrowingReader:
        return GrowingReader(self)

    def _wait_until(self, size: int) -> bool:
        """Wait until `size` bytes have arrived. False if the upload ended with fewer."""
        with self._cond:
            while True:
                if self._error:
                    raise UploadError(self._error)
                if self.received >= size:
                    return True
                if self._finished:
                    return False
                self._cond.wait(timeout=0.25)

    def faststart(self) -> bool:
        """True for an MP4/MOV whose index (moov) comes before the media data (mdat).

        Such a file can be decoded straight through while it is still arriving, as long as the
        reader says it cannot seek: FFmpeg otherwise jumps to the end of the media data, which
        has not been uploaded yet. Anything else (index at the end, other containers) must wait
        for the whole upload.
        """
        if self._faststart is not None:
            return self._faststart
        found = False
        position, saw_moov = 0, False
        with self.path.open("rb") as handle:
            for _ in range(64):
                if not self._wait_until(min(position + 16, self.total)):
                    break
                size, kind = _read_box_header(handle, position)
                if len(kind) < 4 or (position == 0 and kind != b"ftyp"):
                    break
                if kind == b"mdat":
                    found = saw_moov
                    break
                saw_moov = saw_moov or kind == b"moov"
                if size < 8:
                    break
                position += size
        self._faststart = found
        return found

    def _wait_for(self, position: int) -> int:
        """Bytes readable from `position`: blocks until some exist or the end is reached."""
        with self._cond:
            while True:
                if self._error:
                    raise UploadError(self._error)
                available = min(self.received, self.total) - position
                if available > 0:
                    return available
                if position >= self.total or self._finished:
                    return 0
                self._cond.wait(timeout=0.25)


def _read_box_header(handle, position: int) -> tuple[int, bytes]:
    handle.seek(position)
    header = handle.read(16)
    size = int.from_bytes(header[:4], "big")
    if size == 1:  # 64-bit size follows the type
        size = int.from_bytes(header[8:16], "big")
    return size, header[4:8]


class GrowingReader:
    """Independent read position over a GrowingFile; has the methods PyAV needs."""

    def __init__(self, source: GrowingFile) -> None:
        self._source = source
        self._handle = source.path.open("rb")
        self._pos = 0
        self.closed = False
        self._seekable: bool | None = None  # decided when first asked, see seekable()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        """Streaming mode (not seekable) only for an index-first MP4 still being uploaded.

        Decided on the first call, which waits for the first bytes of the file."""
        if self._seekable is None:
            self._seekable = self._source.complete or not self._source.faststart()
        return self._seekable

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._pos + offset
        elif whence == os.SEEK_END:
            target = self._source.total + offset
        else:
            raise ValueError(f"bad whence {whence}")
        self._pos = max(0, target)
        return self._pos

    def read(self, size: int = -1) -> bytes:
        remaining_total = max(0, self._source.total - self._pos)
        want = remaining_total if size is None or size < 0 else min(size, remaining_total)
        out = bytearray()
        while want > 0:
            available = self._source._wait_for(self._pos)
            if available == 0:
                break
            take = min(want, available)
            self._handle.seek(self._pos)
            chunk = self._handle.read(take)
            if not chunk:
                break
            out += chunk
            self._pos += len(chunk)
            want -= len(chunk)
            if size is not None and size >= 0:
                break  # a short read is fine; callers loop
        return bytes(out)

    def close(self) -> None:
        if not self.closed:
            self._handle.close()
            self.closed = True

    def __enter__(self) -> GrowingReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
