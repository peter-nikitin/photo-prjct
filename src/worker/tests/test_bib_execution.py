from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest


def test_execution_module_exists():
    from photo_worker import bib_execution

    assert callable(bib_execution.run_bib_recognition)


def test_parent_bounds_output_and_terminates_child_tree(tmp_path: Path):
    from photo_worker.bib_execution import BibExecutionError, _execute

    with pytest.raises(BibExecutionError) as raised:
        _execute(
            [sys.executable, "-c", 'print("x" * 140000)'],
            deadline_seconds=3,
            check_cancelled=lambda: None,
        )
    assert raised.value.code == "output_contract_violation"


def test_deadline_and_lease_loss_terminate_spawned_descendants(tmp_path: Path):
    from photo_worker.bib_execution import BibExecutionError, _execute

    marker = tmp_path / "descendant-survived"
    script = (
        'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'
        + repr(
            "import time,pathlib; time.sleep(0.8); pathlib.Path(" + repr(str(marker)) + ").touch()"
        )
        + "]); time.sleep(10)"
    )
    with pytest.raises(BibExecutionError) as raised:
        _execute([sys.executable, "-c", script], deadline_seconds=0.2, check_cancelled=lambda: None)
    assert raised.value.code == "model_inference_timeout"
    time.sleep(1)
    assert not marker.exists()

    class Lost(Exception):
        pass

    started = time.monotonic()

    def cancel():
        if time.monotonic() - started > 0.2:
            raise Lost()

    with pytest.raises(Lost):
        _execute([sys.executable, "-c", script], deadline_seconds=3, check_cancelled=cancel)
    time.sleep(1)
    assert not marker.exists()


def test_parent_returns_json_only_after_child_exits():
    from photo_worker.bib_execution import _execute

    result = _execute(
        [sys.executable, "-c", "print('{\"candidates\":[]}')"],
        deadline_seconds=3,
        check_cancelled=lambda: None,
    )
    assert result == {"candidates": []}


def test_visual_command_is_loopback_only_and_single_slot(tmp_path: Path):
    from photo_worker.bib_visual import server_command

    command = server_command(model_path=tmp_path, port=12345)
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--threads") + 1] == "2"
    assert "--no-mmproj-offload" in command
    assert "--jinja" in command
    assert "--offline" in command


def test_rejects_downloaded_fingerprint_before_model_loading(tmp_path: Path):
    from photo_worker.bib_execution import BibExecutionError, run_bib_recognition

    source = tmp_path / "source.jpg"
    source.write_bytes(b"bad")
    with pytest.raises(BibExecutionError) as raised:
        run_bib_recognition(source, source_sha256="a" * 64)
    assert raised.value.code == "fingerprint_mismatch"


def test_visual_adapter_preserves_leading_zeros_without_sending_ocr_text(tmp_path: Path):
    import io

    from photo_worker.bib_visual import PROMPT, LlamaVisualReader

    class Opener:
        def open(self, request, timeout):
            assert timeout == 120
            payload = json.loads(request.data)
            assert payload["messages"][0]["content"][0]["text"] == PROMPT
            assert "candidate-number" not in request.data.decode()
            assert payload["temperature"] == 0 and payload["seed"] == 0
            return io.BytesIO(
                b'{"choices":[{"message":{"content":"{\\"numbers\\":[\\"0012\\"]}"}}]}'
            )

    crop = tmp_path / "crop.png"
    crop.write_bytes(b"image")
    reader = LlamaVisualReader(model_path=tmp_path)
    reader._process = object()
    reader._opener = Opener()
    result = reader.read(image_id="candidate-number", image_path=crop)
    assert result.numbers == ("0012",)


def test_visual_adapter_reads_only_bounded_server_response(tmp_path: Path):
    import io

    from photo_worker.bib_visual import LlamaVisualReader, VisualBoundsError

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(b"x" * 20000)

    crop = tmp_path / "crop.png"
    crop.write_bytes(b"image")
    reader = LlamaVisualReader(model_path=tmp_path)
    reader._process = object()
    reader._opener = Opener()
    with pytest.raises(VisualBoundsError, match="16 KiB"):
        reader.read(image_id="id", image_path=crop)


def test_missing_claim_sha_uses_digest_of_verified_download(tmp_path, monkeypatch):
    import hashlib

    from photo_worker.bib_execution import run_bib_recognition

    source = tmp_path / "source.jpg"
    source.write_bytes(b"verified transport bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def execute(command, **kwargs):
        assert command[-2] == expected
        return {"source_sha256": command[-2], "candidates": []}

    monkeypatch.setattr("photo_worker.bib_execution._execute", execute)
    assert run_bib_recognition(source, source_sha256=None)["source_sha256"] == expected


def test_visual_server_lifetime_is_one_photo_and_cleanup_runs_on_failure(tmp_path, monkeypatch):
    import io

    from photo_worker.bib_visual import LlamaVisualReader

    processes = []

    class Process:
        def __init__(self, command, **kwargs):
            self.command = command
            self.terminated = False
            self.waited = False
            processes.append(self)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = True

    class Health(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert request.startswith("http://127.0.0.1:")
            return Health(b"{}")

    monkeypatch.setattr("photo_worker.bib_visual.verify_model_files", lambda *a: None)
    monkeypatch.setattr("subprocess.Popen", Process)
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    for _ in range(2):
        with pytest.raises(ValueError, match="photo failure"):
            with LlamaVisualReader(model_path=tmp_path):
                raise ValueError("photo failure")
    assert len(processes) == 2
    assert all(p.terminated and p.waited for p in processes)


def test_terminal_size_uses_the_same_utf8_bytes_as_http_client():
    import io

    from photo_worker.client import HttpClient
    from photo_worker.runner import _assert_terminal_size

    captured = []

    class Response(io.BytesIO):
        headers = {}

    def opener(request, timeout):
        captured.append(request.data)
        return Response(b"{}")

    payload = {"raw_response": "я" * 100}
    expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    _assert_terminal_size(payload, len(expected))
    HttpClient("https://worker.test", "token", opener=opener).post_json("complete", payload)
    assert captured == [expected]


def test_image_smoke_exercises_current_adaface_and_sface_consumers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from photo_worker import model_smoke
    from photo_worker.face_embedding import FaceEmbeddingError

    path = tmp_path / "smoke.jpg"
    path.write_bytes(b"image")
    calls = []

    def photo(path, **kwargs):
        model = kwargs.get("model", "sface")
        calls.append(("photo", model))
        return SimpleNamespace(model=model, faces=(), warnings=("no_faces_detected",))

    def selfie(path, **kwargs):
        calls.append(("selfie", kwargs.get("model", "sface")))
        raise FaceEmbeddingError("no_face_detected")

    monkeypatch.setattr(model_smoke, "extract_face_embeddings", photo)
    monkeypatch.setattr(model_smoke, "extract_selfie_embedding", selfie)
    model_smoke._assert_photo_embedding_no_face(path)
    model_smoke._assert_selfie_query_no_face(path)
    assert set(calls) == {
        (kind, model)
        for kind in ("photo", "selfie")
        for model in ("sface", "adaface-ir18-webface4m")
    }
