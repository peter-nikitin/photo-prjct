import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agent_api import upload as agent_upload
from agent_api.models import AgentToken
from django.core.management import call_command
from django.utils import timezone
from ingestion import views as upload_views
from ingestion.management.commands import cleanup_stale_uploads
from ingestion.models import UploadBatch, UploadItem
from ingestion.storage import StorageUnavailable, UploadGrant
from ingestion.tests.test_import_flow import ByteStorage
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from picflow.models import Event, EventFolder, Photo
from PIL import Image
from processing.models import PhotoProcessingState


class Storage(ByteStorage):
    def __init__(self, url):
        super().__init__()
        self.url = url

    def create_presigned_post(self, *, incoming_key, max_bytes):
        return UploadGrant(self.url, {"key": incoming_key}, timezone.now() + timedelta(minutes=5))


@pytest.mark.product_flow
def test_detached_upload_stop_resume_and_mcp(
    live_server,
    settings,
    django_user_model,
    tmp_path,
    monkeypatch,
):
    settings.PHOTO_UPLOAD_ENABLED = True
    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_UPLOAD_REGISTRATION_CHUNK = 1
    user = django_user_model.objects.create_superuser("operator")
    secret = AgentToken.issue(owner=user, name="Test laptop")
    event = Event.objects.create(
        name="Race",
        slug="race",
        start_date=date.today(),
        end_date=date.today(),
        timezone_name="Europe/Moscow",
    )
    folder = EventFolder.objects.create(event=event, name="Finish")
    source = tmp_path / "source"
    source.mkdir()
    first, second = source / "first.jpg", source / "second.jpg"
    Image.new("RGB", (16, 16), "red").save(first)
    Image.new("RGB", (16, 16), "blue").save(second)
    original = first.read_bytes()
    keyfile = tmp_path / "credentials"
    keyfile.write_text(secret)
    root = tmp_path / "findme-photo"
    root.mkdir()
    (root / "default.json").write_text(
        json.dumps({"server": live_server.url, "profile": "default"})
    )
    test_dir = Path(__file__).resolve().parent
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path),
        "FINDME_TEST_KEY_FILE": str(keyfile),
        "PYTHON_KEYRING_BACKEND": "fixture_keyring.TestKeyring",
        "PYTHONPATH": os.pathsep.join(map(str, (test_dir, test_dir.parent))),
    }

    created = threading.Event()
    release_creation = threading.Event()
    batch_response = agent_upload.batch_response

    def delay_creation_response(*args, **kwargs):
        if kwargs["status"] == 201:
            created.set()
            assert release_creation.wait(timeout=15)
        return batch_response(*args, **kwargs)

    monkeypatch.setattr(agent_upload, "batch_response", delay_creation_response)
    received = []
    stored = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
            )
            parts = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            assert "Authorization" not in self.headers
            storage.objects[parts["key"].decode()] = parts["file"]
            received.append(parts["file"])
            stored.set()
            if not release.wait(timeout=15):
                self.send_error(500)
                return
            # The object was persisted despite a lost/failed transport response.
            self.send_response(503)
            self.end_headers()

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    storage = Storage(f"http://127.0.0.1:{http.server_port}/")
    monkeypatch.setattr(upload_views, "PrivateUploadStorage", lambda: storage)
    monkeypatch.setattr(cleanup_stale_uploads, "PrivateUploadStorage", lambda: storage)
    confirm = upload_views.confirm_upload_item

    def lose_confirmation_response(**kwargs):
        assert confirm(**kwargs) is not None
        raise StorageUnavailable()

    monkeypatch.setattr(upload_views, "confirm_upload_item", lose_confirmation_response)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def cli(*args, code=0):
        result = subprocess.run(
            [sys.executable, "-m", "findme_client", *map(str, args)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout)

    def await_state(task_id, state):
        deadline = time.monotonic() + 15
        while True:
            payload = cli("upload", "status", task_id)
            if payload["state"] == state:
                return payload
            assert time.monotonic() < deadline, payload
            time.sleep(0.1)

    try:
        task_id = cli("upload", "start", source, "--event", event.pk, "--folder", folder.pk)[
            "task_id"
        ]
        assert created.wait(timeout=10)
        cli("upload", "stop", task_id)
        release_creation.set()
        before_cleanup = await_state(task_id, "stopped")
        batch = UploadBatch.objects.get()
        assert before_cleanup["batch_id"] == str(batch.pk)
        assert not batch.items.exists()
        UploadBatch.objects.filter(pk=batch.pk).update(
            last_activity_at=timezone.now() - timedelta(days=2)
        )
        call_command("cleanup_stale_uploads")
        batch.refresh_from_db()
        assert batch.status == UploadBatch.Status.FAILED

        cli("upload", "resume", task_id)
        assert stored.wait(timeout=10), cli("upload", "status", task_id)
        assert cli("upload", "resume", task_id, code=1)["error"]["code"] == "task_running"
        cli("upload", "stop", task_id)
        release.set()
        stopped = await_state(task_id, "stopped")
        assert stopped["originals"] == {"confirmed": 1, "failed": 0, "unresolved": 1}
        second.unlink()
        cli("upload", "resume", task_id)
        partial = await_state(task_id, "partial")
        assert partial["originals"] == {"confirmed": 1, "failed": 1, "unresolved": 0}
        assert partial["file_errors"] == {"file_changed": 1}
        assert partial["failed_files"] == [{"path": str(second), "error_code": "file_changed"}]
        assert partial["server"]["originals"] == partial["originals"]
        assert partial["batch_id"] == before_cleanup["batch_id"]
        assert received == [original]
        batch = UploadBatch.objects.get()
        assert (batch.uploader, batch.event) == (user, event)
        item = UploadItem.objects.get(status=UploadItem.Status.UPLOADED)
        photo = Photo.objects.get(pk=item.photo_id)
        assert photo.folder == folder
        assert storage.objects[photo.original_key] == original
        assert PhotoProcessingState.objects.filter(photo=photo).exists()
        assert partial["server"]["processing"]
        assert secret.encode() not in (root / task_id / "manifest.sqlite3").read_bytes()

        async def verify_mcp():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "findme_client", "mcp"],
                env=env,
            )
            async with Client(parameters) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                assert names == {
                    "list_events",
                    "get_event",
                    "list_folders",
                    "list_batches",
                    "get_batch",
                    "list_items",
                }
                events = await client.call_tool("list_events", {})
                folders = await client.call_tool("list_folders", {"event_id": event.pk})
                batch_status = await client.call_tool("get_batch", {"batch_id": str(batch.pk)})
                assert event.pk in {row["id"] for row in events.structured_content["results"]}
                assert folders.structured_content["results"][0]["id"] == folder.pk
                assert batch_status.structured_content["originals"] == partial["originals"]
                invalid = await client.call_tool("get_batch", {"batch_id": "invalid"})
                assert invalid.is_error
                assert invalid.structured_content["error"]["code"] == "invalid_batch"

        asyncio.run(verify_mcp())
    finally:
        release_creation.set()
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
