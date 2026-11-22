"""Run the pinned Qwen model independently of the OCR workers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .bib_visual import MODEL_FILE_HASHES

MODEL_DIRECTORY = Path("/worker/models/bib")
PORT = 8180


def verify_model_files(model_path: Path, expected_hashes: dict[str, str]) -> None:
    for name, expected_hash in sorted(expected_hashes.items()):
        path = model_path / name
        if not path.is_file():
            raise RuntimeError(f"missing model file: {name}")
        with path.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"model hash mismatch: {name}")


def server_command(*, model_path: Path, port: int) -> list[str]:
    """The model's packaged chat template and CPU configuration are identity-owned."""
    return [
        "/worker/llama/llama-server",
        "--model",
        str(model_path / "Qwen3VL-2B-Instruct-Q4_K_M.gguf"),
        "--mmproj",
        str(model_path / "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"),
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--parallel",
        "1",
        "--threads",
        "2",
        "--threads-batch",
        "2",
        "--ctx-size",
        "4096",
        "--n-gpu-layers",
        "0",
        "--no-mmproj-offload",
        "--jinja",
        "--no-webui",
        "--offline",
        "--cache-ram",
        "0",
    ]


def main() -> None:
    verify_model_files(MODEL_DIRECTORY, MODEL_FILE_HASHES)
    command = server_command(model_path=MODEL_DIRECTORY, port=PORT)
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
