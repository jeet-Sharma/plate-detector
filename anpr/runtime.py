"""Process-level runtime settings.

``apply_environment`` must run before numpy, cv2 or onnxruntime are imported, because
OpenBLAS reads its thread count at import time. On Windows, leaving it unset makes numpy
commit roughly 500 MB of private memory on a 16-thread machine.
"""

from __future__ import annotations

import os


def physical_cores() -> int:
    """Best-effort physical core count without importing psutil."""
    logical = os.cpu_count() or 1
    return max(1, logical // 2) if logical > 2 else logical


def apply_environment(threads: int = 1) -> None:
    """Pin BLAS/OpenMP thread pools to a small number before heavy imports."""
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = str(max(1, threads))
