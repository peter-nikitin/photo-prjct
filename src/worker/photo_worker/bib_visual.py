from __future__ import annotations

# ruff: noqa: E501 -- the frozen prompt and artifact digests are complete literals.
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROMPT = (  # noqa: E501 - exact frozen prompt from the accepted experiment
    'Look at the runner\'s paper race bib in this crop. Read the MAIN COMPETITOR NUMBER printed prominently on the bib. Ignore the red outline. Exclude telephone/contact details, race distance, year, sponsor text, logos and body parts. Do not guess digits hidden by blur or occlusion. If no main competitor number is visibly readable, return an empty list. If multiple bibs are visible, include only their clearly readable main competitor numbers. Reply only with a JSON object: {"numbers": ["digits"]}. Keep leading zeros if actually printed.'
)
MODEL_REPOSITORY = "Qwen/Qwen3-VL-2B-Instruct-GGUF"
MODEL_REVISION = "d38d39f5972e27cd58023f9b1e9f994b0c85ca47"
LLAMA_CPP_REVISION = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
TEMPERATURE = 0.0
SEED = 0
MAX_TOKENS = 48
MAX_VISUAL_STRINGS = 8
MAX_DIGITS = 16
MAX_RAW_RESPONSE_BYTES = 512
SERVER_RESPONSE_MAX_BYTES = 16384
MODEL_FILE_HASHES = {
    "Qwen3VL-2B-Instruct-Q4_K_M.gguf": "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae",
    "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf": "f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82",
}


class VisualResponseError(ValueError):
    """The visual model returned malformed JSON or an invalid response shape."""


class VisualBoundsError(ValueError):
    """The visual model output exceeded a terminal evidence bound."""


@dataclass(frozen=True)
class VisualReading:
    status: Literal["complete", "uncertain"]
    raw_response: str
    numbers: tuple[str, ...] | None
    error_code: str | None
    inference_ms: float = 0.0

    @classmethod
    def complete(
        cls, raw_response: str, numbers: tuple[str, ...], *, inference_ms: float = 0.0
    ) -> VisualReading:
        _validate_raw(raw_response)
        _validate_numbers(numbers)
        return cls("complete", raw_response, numbers, None, inference_ms)

    @classmethod
    def malformed(cls, *, raw_response: str, error_code: str) -> VisualReading:
        _validate_raw(raw_response)
        return cls("uncertain", raw_response, None, error_code)


def parse_visual_response(raw_response: str) -> tuple[str, ...]:
    _validate_raw(raw_response)
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise VisualResponseError("visual response is not JSON") from error
    if not isinstance(value, dict) or set(value) != {"numbers"}:
        raise VisualResponseError('response must be exactly {"numbers": [...]}')
    numbers = value["numbers"]
    if not isinstance(numbers, list):
        raise VisualResponseError("numbers must be a list")
    result = tuple(numbers)
    _validate_numbers(result)
    return result


def verify_model_files(model_path: Path, expected_hashes: dict[str, str]) -> None:
    for name, expected_hash in sorted(expected_hashes.items()):
        path = model_path / name
        if not path.is_file():
            raise RuntimeError(f"missing model file: {name}")
        with path.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"model hash mismatch: {name}")


def _validate_raw(raw_response: str) -> None:
    if len(raw_response.encode("utf-8")) > MAX_RAW_RESPONSE_BYTES:
        raise VisualBoundsError("visual raw response exceeds 512 UTF-8 bytes")


def _validate_numbers(numbers: tuple[str, ...]) -> None:
    if len(numbers) > MAX_VISUAL_STRINGS:
        raise VisualBoundsError("visual response exceeds 8 strings")
    for number in numbers:
        if not isinstance(number, str) or not number.isascii() or not number.isdigit():
            raise VisualResponseError("visual numbers must be ASCII digit strings")
        if len(number) > MAX_DIGITS:
            raise VisualBoundsError("visual digit string exceeds 16 digits")


def server_command(*, model_path: Path, port: int) -> list[str]:
    """The model's packaged chat template and CPU configuration are identity-owned."""
    return [
        "/worker/llama/llama-server",
        "--model",
        str(model_path / "Qwen3VL-2B-Instruct-Q4_K_M.gguf"),
        "--mmproj",
        str(model_path / "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"),
        "--host",
        "127.0.0.1",
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
    ]


class LlamaVisualReader:
    """One loopback server per photo; requests and model lifetime remain sequential."""

    def __init__(self, *, model_path: Path) -> None:
        self._model_path = model_path
        self._process: subprocess.Popen[bytes] | None = None
        self._port = 0

    def __enter__(self) -> LlamaVisualReader:
        import socket
        import subprocess
        import time
        from urllib.error import URLError
        from urllib.request import ProxyHandler, build_opener

        verify_model_files(self._model_path, MODEL_FILE_HASHES)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self._port = listener.getsockname()[1]
        self._opener = build_opener(ProxyHandler({}))
        # Inherit the inference child's process group so the parent can kill the entire tree.
        self._process = subprocess.Popen(
            server_command(model_path=self._model_path, port=self._port),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            started = time.monotonic()
            while time.monotonic() - started < 90:
                if self._process.poll() is not None:
                    raise RuntimeError("llama_server_exited")
                try:
                    with self._opener.open(
                        f"http://127.0.0.1:{self._port}/health", timeout=0.5
                    ) as response:
                        if response.status == 200:
                            return self
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            raise TimeoutError("llama_server_start_timeout")
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        import subprocess

        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None

    def read(self, *, image_id: str, image_path: Path) -> VisualReading:
        import base64
        import time
        from urllib.request import Request

        del image_id
        if self._process is None:
            raise RuntimeError("llama_server_not_started")
        # Crops are <=640px; cap encoded input independently of image dimensions.
        with image_path.open("rb") as stream:
            encoded = stream.read(2 * 1024 * 1024 + 1)
        if len(encoded) > 2 * 1024 * 1024:
            raise VisualBoundsError("visual input exceeds 2 MiB")
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(encoded).decode("ascii")
                            },
                        },
                    ],
                }
            ],
            "temperature": TEMPERATURE,
            "seed": SEED,
            "max_tokens": MAX_TOKENS,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            f"http://127.0.0.1:{self._port}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        with self._opener.open(request, timeout=120) as response:
            data = response.read(SERVER_RESPONSE_MAX_BYTES + 1)
        if len(data) > SERVER_RESPONSE_MAX_BYTES:
            raise VisualBoundsError("server response exceeds 16 KiB")
        try:
            raw = json.loads(data)["choices"][0]["message"]["content"]
            if not isinstance(raw, str):
                raise ValueError("invalid content")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise RuntimeError("invalid_llama_response") from error
        try:
            numbers = parse_visual_response(raw)
        except VisualResponseError:
            return VisualReading.malformed(raw_response=raw, error_code="malformed_response")
        return VisualReading.complete(
            raw, numbers, inference_ms=(time.perf_counter() - started) * 1000
        )
