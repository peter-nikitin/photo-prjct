"""Build-time guarantees for the worker's CPU-only runtime payload."""

from __future__ import annotations

from collections.abc import Iterable
from importlib import import_module, metadata
from pathlib import Path
from site import getsitepackages
from typing import Protocol

MAX_RUNTIME_BYTES = 2048 * 1024 * 1024
MODEL_DIRECTORIES = (Path("/worker/models"),)


class TorchVersion(Protocol):
    cuda: str | None


class TorchModule(Protocol):
    version: TorchVersion


def _directory_size(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def verify_runtime_contract(
    *,
    torch_module: TorchModule | None = None,
    site_package_directories: Iterable[Path] | None = None,
    model_directories: Iterable[Path] = MODEL_DIRECTORIES,
    distributions: Iterable[metadata.Distribution] | None = None,
) -> None:
    """Reject CUDA payloads and an oversized installed worker runtime."""
    torch_module = torch_module or import_module("torch")
    if torch_module.version.cuda is not None:
        raise RuntimeError("Torch CUDA runtime must not be installed")

    installed_distributions = (
        distributions if distributions is not None else metadata.distributions()
    )
    nvidia_distributions = sorted(
        distribution.metadata.get("Name", "")
        for distribution in installed_distributions
        if distribution.metadata.get("Name", "").lower().startswith("nvidia-")
    )
    if nvidia_distributions:
        raise RuntimeError(f"NVIDIA distributions must not be installed: {nvidia_distributions}")

    site_package_directories = (
        site_package_directories
        if site_package_directories is not None
        else (Path(directory) for directory in getsitepackages())
    )
    runtime_directories = (*site_package_directories, *model_directories)
    runtime_bytes = sum(_directory_size(directory) for directory in runtime_directories)
    if runtime_bytes > MAX_RUNTIME_BYTES:
        raise RuntimeError("Worker site-packages and models exceed 2048 MiB")


if __name__ == "__main__":
    verify_runtime_contract()
