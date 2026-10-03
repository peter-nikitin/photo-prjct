"""Keep native inference pools within the worker container's CPU budget."""

from __future__ import annotations

import os
import threading
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


# Two bulk slots share the existing two-vCPU container; native libraries must not
# each reserve the full CPU budget independently.
CPU_THREADS = 1 if os.environ.get("PHOTO_WORKER_POOL") == "bulk" else cpu_thread_budget()


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
_TORCH_CONFIG_LOCK = threading.Lock()


def configure_torch(torch: Any) -> None:
    global _TORCH_CONFIGURED
    with _TORCH_CONFIG_LOCK:
        if not _TORCH_CONFIGURED:
            torch.set_num_threads(CPU_THREADS)
            torch.set_num_interop_threads(1)
            _TORCH_CONFIGURED = True
