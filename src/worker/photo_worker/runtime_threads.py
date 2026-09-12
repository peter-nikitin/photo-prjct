"""Keep native inference pools within this single worker's container CPU budget."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

CPU_MAX_PATH = Path("/sys/fs/cgroup/cpu.max")


def cpu_thread_budget(path: Path = CPU_MAX_PATH) -> int:
    """Support the deployed 1/2-CPU envelope; unbounded build/local processes use one."""
    try:
        quota, period = path.read_text().split()
        if quota == "max":
            return 1
        return max(1, min(2, int(quota) // int(period)))
    except (OSError, ValueError, ZeroDivisionError):
        return 1


CPU_THREADS = cpu_thread_budget()


def configure_native_environment() -> None:
    # This runs at package import, before NumPy/OpenCV/ORT/PyTorch import native code.
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENCV_FOR_THREADS_NUM",
    ):
        os.environ[name] = str(CPU_THREADS)


_TORCH_CONFIGURED = False


def configure_torch(torch: Any) -> None:
    global _TORCH_CONFIGURED
    if not _TORCH_CONFIGURED:
        torch.set_num_threads(CPU_THREADS)
        torch.set_num_interop_threads(1)
        _TORCH_CONFIGURED = True
