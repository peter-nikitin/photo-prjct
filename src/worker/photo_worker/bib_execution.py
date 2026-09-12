"""Bounded per-photo process isolation, including the nested llama.cpp server."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RESULT_BYTES = 120 * 1024
MODEL_DIRECTORY = Path("/worker/models/bib")
OCR_CONFIG = Path(__file__).with_name("bib_ocr_config.json")


@dataclass
class BibExecutionError(Exception):
    code: str


@contextlib.contextmanager
def _termination_cleanup() -> Iterator[None]:
    """Turn daemon SIGTERM into stack unwinding before the supervisor exits."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    # Always kill the group, even when its leader already exited with a surviving child.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)


def _execute(
    command: list[str], *, deadline_seconds: float, check_cancelled: Callable[[], None]
) -> dict[str, Any]:
    output = bytearray()
    deadline = time.monotonic() + deadline_seconds
    with _termination_cleanup():
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                eof = False
                while not eof or process.poll() is None:
                    check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BibExecutionError("model_inference_timeout")
                    for key, _ in selector.select(min(0.1, remaining)):
                        chunk = os.read(key.fd, min(8192, MAX_RESULT_BYTES + 1 - len(output)))
                        if not chunk:
                            eof = True
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > MAX_RESULT_BYTES:
                            raise BibExecutionError("output_contract_violation")
                    if eof and process.poll() is None:
                        time.sleep(min(0.01, remaining))
            check_cancelled()
            if process.returncode != 0:
                raise BibExecutionError("model_inference_error")
            try:
                result = json.loads(output)
            except (ValueError, UnicodeError) as error:
                raise BibExecutionError("model_inference_error") from error
            if not isinstance(result, dict):
                raise BibExecutionError("output_contract_violation")
            if set(result) == {"error_code"}:
                code = result["error_code"]
                if code not in {
                    "output_contract_violation",
                    "decode_failed",
                    "model_inference_timeout",
                    "model_inference_error",
                }:
                    code = "model_inference_error"
                raise BibExecutionError(code)
            return result
        finally:
            _kill_tree(process)
            if process.stdout is not None:
                process.stdout.close()


def run_bib_recognition(
    source_path: Path,
    *,
    source_sha256: str | None,
    deadline_seconds: int = 300,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    check_cancelled()
    if source_path.stat().st_size > 50 * 1024 * 1024:
        raise BibExecutionError("input_too_large")
    with source_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if source_sha256 is not None and digest != source_sha256:
        raise BibExecutionError("fingerprint_mismatch")
    with tempfile.TemporaryDirectory(prefix="bib-inference-") as working_directory:
        return _execute(
            [
                sys.executable,
                "-m",
                "photo_worker.bib_execution",
                str(source_path),
                digest,
                working_directory,
            ],
            deadline_seconds=deadline_seconds,
            check_cancelled=check_cancelled,
        )


def _child(source_path: str, source_sha256: str, working_directory: str) -> None:
    tempfile.tempdir = working_directory
    from PIL import UnidentifiedImageError

    from .bib_recognition import (
        BibBoundsError,
        RapidOCREngine,
        recognize_photo,
        verify_runtime_versions,
    )
    from .bib_visual import LlamaVisualReader, VisualBoundsError

    result: dict[str, Any]
    # Third-party logs cannot enter the bounded JSON result stream or reveal image evidence.
    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        try:
            verify_runtime_versions()
            ocr = RapidOCREngine.from_experiment_config(OCR_CONFIG, repository=MODEL_DIRECTORY)
            with LlamaVisualReader(model_path=MODEL_DIRECTORY) as visual:
                result = recognize_photo(
                    Path(source_path), source_sha256=source_sha256, ocr=ocr, visual=visual
                ).as_dict()
        except (BibBoundsError, VisualBoundsError):
            result = {"error_code": "output_contract_violation"}
        except UnidentifiedImageError:
            result = {"error_code": "decode_failed"}
        except TimeoutError:
            result = {"error_code": "model_inference_timeout"}
        except Exception:
            result = {"error_code": "model_inference_error"}
    encoded = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > MAX_RESULT_BYTES:
        encoded = b'{"error_code":"output_contract_violation"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    _child(*sys.argv[1:])
