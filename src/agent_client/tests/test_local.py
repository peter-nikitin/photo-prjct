import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image


def test_inspect_reports_jpeg_and_skips_files_and_symlinks(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    photo = source / "photo.JPG"
    Image.new("RGB", (16, 16)).save(photo)
    (source / "ignored.txt").write_text("notes")
    (source / "alias.jpg").symlink_to(photo)
    client = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "findme_client", "upload", "inspect", str(source)],
        env={**os.environ, "PYTHONPATH": str(client)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "file_count": 1,
        "total_bytes": photo.stat().st_size,
        "skipped_count": 2,
    }


def test_task_in_running_state_without_lock_is_interrupted(tmp_path, monkeypatch):
    from findme_client.tasks import Original, Task

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 16)).save(source)
    task = Task.create(
        profile="default",
        server="https://photos.example",
        owner=1,
        event=1,
        folder=None,
        originals=[Original.capture(source, 1024)],
    )
    task.record_state("running")
    assert task.summary()["state"] == "interrupted"


def test_storage_multipart_yields_bounded_chunks(tmp_path):
    import httpx
    from findme_client.api import send_original

    original = tmp_path / "photo.jpg"
    original.write_bytes(b"data" * (1024 * 1024))
    sent = 0

    class StreamingTransport(httpx.BaseTransport):
        def handle_request(self, request):
            nonlocal sent
            assert "Authorization" not in request.headers
            for chunk in request.stream:
                assert len(chunk) <= 1024 * 1024
                sent += len(chunk)
            return httpx.Response(204)

    with httpx.Client(transport=StreamingTransport()) as client, original.open("rb") as source:
        send_original(
            client, {"url": "https://storage.example", "fields": {"key": "x"}}, "photo.jpg", source
        )
    assert sent > original.stat().st_size
