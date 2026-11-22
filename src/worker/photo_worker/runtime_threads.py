"""Keep native inference pools within the worker container's CPU budget."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from photo_worker.transport import POOL_IDENTITIES

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


_CPU_BUDGET = cpu_thread_budget()


def has_mixed_bib_types(processor_types: tuple[str, ...]) -> bool:
    return len(processor_types) > 1 and "bib_recognition" in processor_types


def worker_slot_count() -> int:
    if os.environ.get("PHOTO_WORKER_TRANSPORT", "local") == "remote":
        return 2 if os.environ.get("PHOTO_WORKER_POOL") == "bulk" else 1
    raw_types = os.environ.get("PHOTO_WORKER_PROCESSOR_TYPES", "")
    if has_mixed_bib_types(tuple(item.strip() for item in raw_types.split(","))):
        return 2
    if (
        _CPU_BUDGET == 2
        and os.environ.get("PHOTO_WORKER_PROCESSOR_IDENTITIES", "").strip()
        == POOL_IDENTITIES["selfie"]
        and os.environ.get("PHOTO_WORKER_PROCESSOR_TYPES", "").strip() == "selfie_query"
    ):
        return 2
    return 1


# Each slot gets a share of the total budget, including the existing remote bulk slots.
CPU_THREADS = max(1, _CPU_BUDGET // worker_slot_count())


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
